**English** · [日本語](BE-XXXX-approval-time-be-id-allocation-ja.md)

# BE-XXXX — Approval-time BE-ID allocation with auto-merge

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-approval-time-be-id-allocation.md) |
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

This item moves allocation from PR-open to **PR approval**, and pairs it with **auto-merge**, so a
`BE-NNNN` is assigned only to an item a reviewer has accepted, and lands on `main` moments later. An
item keeps the `BE-XXXX` placeholder throughout authoring and review; the real number is allocated
when the PR is approved, then auto-merge brings the branch in once the required checks pass. The goal
is that the `BE-NNNN` sequence on `main` stays **contiguous** and a number is never burned by a
proposal that is rejected or abandoned.

This revisits an alternative BE-0061 weighed and set aside — "assign the real id only at merge time,
keeping `BE-XXXX` through review" — on the strength of two things that answer its objections: the
slug already gives every item a stable, citable handle during review, and GitHub's native auto-merge
(and merge queue) make a deferred-then-auto-merged flow practical without hand-holding. It is
contributor-workflow infrastructure only: no LLM enters any path, `run` and CI stay deterministic,
and nothing app-specific moves into the tool, so it touches none of the prime directives. It is a
direct sibling of BE-0061 — that item made allocation *collision-proof*; this one makes it
*acceptance-gated*.

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
confusion and waste a citable number. This item removes the gap **by construction**: a number is only
ever handed to an accepted item, and it reaches `main` immediately after.

### BE-0061's two objections are now answerable

BE-0061 set this idea aside for two reasons, both of which have an answer today:

- **"Loses the stable, referenceable id during review."** The id is not the only stable handle: the
  **slug** is unique and permanent per item, and the allocator already keys every operation on it
  (the directory `BE-XXXX-<slug>`, the index row, the cross-reference rewrite). During review the
  item is cited as `BE-XXXX-<slug>` and the PR title keeps its `[BE-XXXX]` prefix; the *number* only
  becomes meaningful once the item is accepted, which is exactly when it is now assigned. Nothing a
  reviewer needs to reference is lost — only the premature number is deferred.
- **"Awkward on plain GitHub, which cannot rewrite a merged tree without a merge queue."** This is no
  longer a blocker: allocation still happens **pre-merge** (on the branch, at approval), and GitHub's
  native **auto-merge** then merges the branch once checks pass. We never rewrite a *merged* tree — we
  rewrite the branch, then merge it — so the objection's premise does not apply.

## Detailed design

The change is confined to the workflow layer and the authoring docs. The allocation logic
([`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py)) and the entire
race-hardening apparatus from BE-0061 are reused unchanged — only *when* allocation runs, and what
happens immediately after, change.

### Trigger: allocate on approval, not on open

`roadmap-id`'s trigger moves from `pull_request` (opened / synchronize) to `pull_request_review`
with `types: [submitted]`, gated on `github.event.review.state == 'approved'` (keeping the existing
`head.repo.full_name == github.repository` same-repo guard, since the bot cannot push to a fork). The
body of the job is the same as today: build the reserved set from open PRs and the claims ledger,
run the allocator, claim each id atomically with the retry loop, regenerate the index, commit, and
push the rename back to the branch; then rewrite the PR title's `BE-XXXX` to the allocated id.

Through authoring and review the item stays `BE-XXXX-<slug>`; the placeholder is what reviewers see.

### Auto-merge: land it contiguously

After the rename is pushed and the title rewritten, the workflow enables auto-merge on the PR
(`gh pr merge --auto` with the repo's merge strategy). The PR then merges by itself once the required
checks pass on the allocation commit. Because allocation now happens at approval — moments before the
merge — the window between "number assigned" and "number on `main`" shrinks to near-zero, so the
out-of-order-merge gap described above essentially cannot open.

### The post-approval commit vs. stale-review dismissal (the crux)

Allocating *after* approval means the allocator pushes a github-actions[bot] commit on top of the
approved head. Under branch protection's "dismiss stale pull request approvals when new commits are
pushed", that bot commit dismisses the human approval and stalls auto-merge — the central wrinkle of
this design. The options, with a recommendation:

1. **Auto-merge with stale-approval dismissal turned off (recommended).** The only automated commit
   pushed after approval is the allocator's well-scoped rename (directory/file move, in-file
   `BE-XXXX` → `BE-NNNN`, index regeneration). Treating that as not invalidating the review lets
   auto-merge proceed. The trade-off — any post-approval push keeps the approval — is acceptable for
   this repo's same-repo `claude/*` / `<user>/<topic>` flow, and is documented as deliberate policy.
2. **Bot re-affirmation.** After pushing, the workflow submits an approving review via the bot token.
   Partial: a `GITHUB_TOKEN` review does not satisfy a required *human* / CODEOWNER approval, and
   GitHub rulesets cannot currently exempt stale-dismissal by commit author, so this only helps where
   the required approval count can be met by the bot.
3. **Allocate inside a merge queue.** Rejected: the merge queue merges the branch *as-is* and cannot
   fold in a content rewrite, so allocation cannot run there.

The recommended wiring is option 1 (auto-merge + no stale-approval dismissal). The exact
branch-protection / ruleset configuration is **TBD**, pending the maintainer's chosen protection
setup; the workflow change above is independent of which option lands.

### What stays the same (defense in depth)

The BE-0061 hardening is kept verbatim: atomic `refs/be-claims/*` claims, the
[`roadmap-id-repair`](../../../.github/workflows/roadmap-id-repair.yml) backstop, and
[`roadmap-claims-gc`](../../../.github/workflows/roadmap-claims-gc.yml). With approval-time
allocation far fewer PRs hold a real number at once, so the same-window race is rarer — but the
claims ledger and repair stay as defense in depth, and `roadmap-claims-gc`'s release-on-close still
reaps a claim from a PR closed between approval and merge. `allocate_roadmap_ids.py` remains pure
(reserved set via the environment, no GitHub calls).

### Authoring-rule and docs updates

The authoring rules in [`CLAUDE.md`](../../../CLAUDE.md),
[`roadmaps/README.md`](../../README.md) / [`README-ja.md`](../../README-ja.md), and
[`docs/ai-development.md`](../../../docs/ai-development.md) are updated to state that an item — and
its PR title — carries the `BE-XXXX` placeholder **through review**, and that the real id is allocated
on approval (not on open). The `ideation` skill already authors with `BE-XXXX`, so its drafting flow
is unchanged; only the note about *when* CI rewrites the number is updated.

### Prime-directive compliance

Contributor-workflow infrastructure only. No LLM is added to any path; `run` and CI stay
deterministic; nothing app-specific moves into the tool, drivers, or runner. It sits in the same
family as
[BE-0043](../../implemented/BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md),
BE-0061,
[BE-0074](../../implemented/BE-0074-be-template-standardization/BE-0074-be-template-standardization.md),
and [BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md).

## Alternatives considered

- **Keep PR-open allocation; switch to smallest-free instead of `max + 1`.** Far lower churn — no
  trigger change, no auto-merge, and the review-time number is preserved — and it would fill the hole
  a rejected lower-numbered PR leaves. Rejected as the primary path because reusing a number whose
  `[BE-00xx]` already appeared in a rejected PR's title and review thread makes that id *ambiguous*
  in project history, which is precisely what the "never reuse a number" rule exists to prevent. It
  remains a reasonable lighter-weight fallback if approval-time allocation is judged too invasive.
- **Accept gaps as harmless; document and stop.** Cheapest of all: ids are permanent and monotonic by
  design, and Swift-Evolution tolerates gaps, so a hole from a rejected proposal is defensible.
  Rejected against the stated goal of keeping `main`'s numbering contiguous by construction.
- **Allocate at merge time via a merge-queue rewrite.** Not possible on plain GitHub: the merge queue
  merges the branch as-is and cannot rewrite content, so the number could not be folded in at the
  queue. (This is the form BE-0061 called "awkward without a merge queue"; approval-time pre-merge
  allocation sidesteps it.)
- **Leave BE-0061's decision untouched (do nothing).** Rejected: the spent-id and non-contiguous
  concern is real, if minor, and is removable without reopening any of BE-0061's race guarantees.

## References

- [BE-0061 — Collision-proof BE-ID allocation](../../implemented/BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
  — the item this extends; its *Alternatives considered* records the "assign id at merge time" option
  this revisits, and its hardening (atomic claims, repair, claims-gc) is reused unchanged.
- [`.github/workflows/roadmap-id.yml`](../../../.github/workflows/roadmap-id.yml) (the trigger this
  moves to approval),
  [`roadmap-id-repair.yml`](../../../.github/workflows/roadmap-id-repair.yml),
  [`roadmap-claims-gc.yml`](../../../.github/workflows/roadmap-claims-gc.yml) — the workflows in scope.
- [`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py) (reused unchanged),
  [`scripts/be_claims.sh`](../../../scripts/be_claims.sh) (the claims ledger).
- [`CLAUDE.md`](../../../CLAUDE.md) ·
  [`roadmaps/README.md`](../../README.md) ·
  [`docs/ai-development.md`](../../../docs/ai-development.md) — the authoring rules updated to defer the
  number to approval.
- GitHub docs — *Automatically merging a pull request* (auto-merge) and *Managing a merge queue* — the
  native mechanisms this relies on.

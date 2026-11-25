**English** · [日本語](BE-0061-be-id-allocation-hardening-ja.md)

# BE-0061 — Collision-proof BE-ID allocation (atomic reservation + auto-repair)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0061](BE-0061-be-id-allocation-hardening.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0061") |
| Implementing PR | [#175](https://github.com/bajutsu-e2e/bajutsu/pull/175), [#436](https://github.com/bajutsu-e2e/bajutsu/pull/436) (retirement) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Every roadmap item carries a permanent, monotonically increasing `BE-NNNN` id. The `roadmap-id` workflow assigns it at PR time from the `BE-XXXX` placeholder that the ideation skill leaves behind, so no author guesses a number.

That allocator avoided ids already on `origin/main`. It also avoided, on a best-effort basis, ids on other open PRs, through a list passed in as `ROADMAP_RESERVED_IDS`. A `roadmap-id-repair` workflow fixed a collision *against main* after a roadmap PR merged.

Two holes remained:

- Two PRs that allocate in the **same window** could still take the same number. A placeholder carries no digits, so the reserved-id list cannot see an id that has not been assigned yet.
- A number contested **only between open PRs**, with none merged, had no arbiter. Repair's authority was `main` alone, so it never fired.

The three open PRs that each held `BE-0056` (#166, #169, and #170, repaired by hand on 2026-06-21) are the failure that these holes produce.

This item closes both holes. Allocation now **claims each id atomically** as a `refs/be-claims/*` git ref, so two branches in the same window cannot both take a number. Repair becomes the **backstop** for anything that still slips through, such as a hand-typed id or a branch that predates the machinery. Its authority becomes `main` first. Failing that, the authority is the **lowest open-PR number** that holds the id. Repair runs on a schedule as well as on merge.

This item is purely contributor-facing infrastructure. No tool behavior, runtime, or scenario semantics change, and the deterministic gate is untouched. It is a direct sibling of [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md). BE-0043 made the *roadmap files* conflict-resistant, and this item makes their *ids* collision-proof.

**Update:** The reservation ledger and the auto-repair backstop described below were later retired. [BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md) moved allocation to merge time on `main`. Serialized merge-order allocation reads the latest `main` and hands out one number at a time. Two branches can no longer contend for a number, so the ledger and its repair became redundant. Only the pure allocator (`allocate_roadmap_ids.py`, allocate-only now) and the merge-time `roadmap-id` workflow remain.

## Motivation

- **The same-window allocation race was still open.** `ROADMAP_RESERVED_IDS` is built from other open PRs' files, meaning their `BE-NNNN` directories. A PR that still holds a `BE-XXXX` placeholder contributes no digits. Two PRs whose `roadmap-id` runs overlap each see the other as numberless, and both pick `max + 1`. The reservation narrowed the race but never closed it.
- **An open-PR-only collision had no fix.** `roadmap-id-repair` keyed entirely on `origin/main`. It renumbered an item whose number a *merged* item now held. When several open PRs share a number and none has merged, nothing is authoritative, so the repair was a no-op. The `BE-0056` three-way collision was exactly this case, and it had to be resolved by hand.
- **Hand-typed concrete ids bypass allocation entirely.** An item committed with a literal `BE-NNNN`, instead of the `BE-XXXX` placeholder, never runs through the allocator. Neither the reservation nor the merge-time repair would catch a number that such an item duplicates.

## Detailed design

### Atomic reservation — the `refs/be-claims/*` ledger

A claim is a git ref named `refs/be-claims/<NNNN>`. GitHub's create-ref API (`POST /repos/{owner}/{repo}/git/refs`) returns `422` if the ref already exists. That makes the API a compare-and-set: the first PR to claim a number wins, and a second PR gets `422` and re-picks. The helper `scripts/be_claims.sh` wrapped `list`, `claim`, and `release` over this API and `git ls-remote`.

The `roadmap-id` workflow folds the claims ledger into the reserved set, allocates, and then claims each allocated id atomically. If another PR races ahead and the workflow loses a claim, the workflow releases any ids that it did win, resets, and retries. The winning claim is now visible, so the next attempt steers off it.

Allocation keeps the same monotonic rule: `max(used) + 1`, skipping any reserved or claimed number. [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) **stays pure**. It receives the reserved set through the environment and never talks to GitHub. The API calls and the retry loop live in the workflow, exactly as the existing reservation does.

### Claim lifecycle

A claim earns its keep only while an *open* PR holds the id and the id is *not yet on* `main`. Two triggers in `roadmap-claims-gc.yml` enforced that:

- **Release on close.** When a roadmap PR closes, merged or not, the workflow drops the claims that the PR introduced. A merged id now lives on `main`, so its claim is redundant. An abandoned id is freed.
- **Daily sweep.** A scheduled job drops any claim whose id is on `main` or held by no open PR. It reaps a leak from, for example, a cancelled allocation run.

### Repair authority — `main`, else the lowest open-PR number

`allocate_roadmap_ids.py --repair` renumbers an item that the branch *introduces* (a slug not yet on `main`) when its number is already taken. The authority decides who keeps a contested number. It generalizes from "`main` only" to two rules, tried in order:

1. `origin/main`, if a *different* item already holds the number there. A merged item always wins.
2. Otherwise, the **lowest open-PR number** that holds the number. The workflow passes it in as `ROADMAP_LOWER_PR_IDS`, computed from `scripts/open_pr_be_map.sh`.

The branch renumbers only when it is *not* the authority. An item whose slug is already on `main` is one that the branch inherited. A rebase resolves that case, and no renumbering happens.

The `roadmap-id-repair` workflow now runs on a **daily schedule** as well as on a push to `main`. An open-PR-only collision is caught even when nothing merges. The sweep reserves each newly assigned id. It claims the id and folds it into the reservations for the rest of the run, so two losers always land on distinct numbers. The contested id's claim stays with the authority.

### Fork PRs

The bot's token cannot push to a fork's branch or create refs in this repo. Both workflows therefore act only on same-repo PRs, under the existing `head.repo.full_name == github.repository` guard. The project's PRs are same-repo `claude/*` or `<user>/<topic>` branches, so this is the normal path. A fork contributor's ids are reconciled when a maintainer brings the branch in-repo.

## Alternatives considered

- **Serialize allocation with a global `concurrency` group instead of claims.** This is cheaper, but it falls short on two counts. GitHub keeps only one *pending* run per concurrency group and cancels older pending ones, so three PRs opened at once can have an allocation silently dropped. Serialization also gives no authority for an open-PR-only collision. Atomic claims hold unconditionally and need no serialization.
- **Assign the real id only at merge time, keeping `BE-XXXX` through review.** This removes the race by serializing on `main`. It loses the stable, referenceable id during review, because the repo prefixes PR titles with the id and reviewers cite it. It is also awkward on plain GitHub, which cannot rewrite a merged tree without a merge queue.
- **Switch to a counter-free id scheme** (content hash, ULID, or per-author ranges). Rejected. The roadmap is built on permanent, human-citable, monotonic `BE-NNNN` ids, in the Swift-Evolution tradition. Changing the scheme is large, irreversible churn for no reader benefit.
- **Detect collisions with a failing check and fix only by hand.** A loud red check is a weaker backstop, and it adds manual toil on every slip. Auto-repair fixes the collision without intervention. A local `make roadmap-id-repair` target was also provided for deliberate, hands-on fixes.
- **Author this as a Proposal first (a `BE-XXXX` placeholder).** Unnecessary here. The work is implemented in the same change, so it is filed directly as Implemented under *Development infrastructure*, a sibling to BE-0043. That is the born-implemented path the repo already uses.

## Progress

- [x] Shipped — see the *Implementing PR* above.
- [x] Reservation ledger + auto-repair retired as redundant under merge-time allocation (BE-0089):
  removed `scripts/be_claims.sh`, `scripts/open_pr_be_map.sh`, `scripts/open_pr_be_ids.sh`, the
  `roadmap-id-repair` / `roadmap-claims-gc` workflows, and the allocator's `--repair` path.

## References

- [BE-0043 — Conflict-resistant file flow](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md): the sibling that this item extends from files to ids (self-healing hooks, generated indexes, merge drivers).
- [CLAUDE.md](../../CLAUDE.md) and [docs/ai-development.md](../../docs/ai-development.md): the roadmap ID rules and the allocation and repair flow that this item hardens.
- [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) (allocation) and [`tests/test_allocate_roadmap_ids.py`](../../tests/test_allocate_roadmap_ids.py). The claims ledger (`scripts/be_claims.sh`) and the open-PR tiebreaker input (`scripts/open_pr_be_map.sh`) were removed when the reservation layer was retired (see *Progress*).
- [`.github/workflows/roadmap-id.yml`](../../.github/workflows/roadmap-id.yml): the merge-time allocator. The `roadmap-id-repair` and `roadmap-claims-gc` workflows were removed with the reservation layer.

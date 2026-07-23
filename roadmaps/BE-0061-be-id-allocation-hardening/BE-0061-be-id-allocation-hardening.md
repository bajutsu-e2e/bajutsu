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

Every roadmap item carries a permanent, monotonically increasing `BE-NNNN` id. The `roadmap-id`
workflow assigns it at PR time from the `BE-XXXX` placeholder the ideation skill leaves behind, so no
author guesses a number. That allocator avoided ids already on `origin/main` and — best effort — on
other open PRs (a list passed in via `ROADMAP_RESERVED_IDS`), and a `roadmap-id-repair` workflow
fixed a collision *against main* after a roadmap PR merged.

Two holes remained. Two PRs allocating in the **same window** could still take the same number,
because a placeholder carries no digits — the reserved-id list cannot see an id that has not been
assigned yet. And a number contested **only between open PRs**, with none merged, had no arbiter:
repair's authority was `main` alone, so it never fired. The three open PRs that each held `BE-0056`
(#166/#169/#170, repaired by hand on 2026-06-21) are the failure these holes produce.

This item closes both. Allocation now **claims each id atomically** as a `refs/be-claims/*` git ref,
so two branches in the same window cannot both take a number. Repair is generalized into the
**backstop** for anything that still slips through (a hand-typed id, a branch predating the
machinery): its authority becomes `main` first, else the **lowest open-PR number** holding the id,
and it runs on a schedule as well as on merge. This is purely contributor-facing infrastructure — no
tool behavior, runtime, or scenario semantics change, and the deterministic gate is untouched. It is
a direct sibling of [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md),
which made the *roadmap files* conflict-resistant; this makes their *ids* collision-proof.

**Update:** the reservation ledger and the auto-repair backstop described below were later retired,
once [BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md) moved
allocation to merge time on `main`. Serialized merge-order allocation reads the latest `main` and
hands out one number at a time, so two branches can no longer contend for a number — the ledger and
its repair became redundant. Only the pure allocator (`allocate_roadmap_ids.py`, allocate-only now)
and the merge-time `roadmap-id` workflow remain.

## Motivation

- **The same-window allocation race was still open.** `ROADMAP_RESERVED_IDS` is built from other open
  PRs' files (their `BE-NNNN` directories). A PR still holding a `BE-XXXX` placeholder contributes no
  digits, so two PRs whose `roadmap-id` runs overlap each see the other as numberless and both pick
  `max + 1`. The reservation narrowed the race but never closed it.
- **An open-PR-only collision had no fix.** `roadmap-id-repair` keyed entirely on `origin/main`: it
  renumbered an item whose number a *merged* item now held. When several open PRs share a number and
  none has merged, nothing is authoritative, so the repair was a no-op — exactly the `BE-0056`
  three-way collision, which had to be resolved by hand.
- **Hand-typed concrete ids bypass allocation entirely.** An item committed with a literal `BE-NNNN`
  (instead of the `BE-XXXX` placeholder) never runs through the allocator, so neither the reservation
  nor the merge-time repair would catch a number it duplicates.

## Detailed design

### Atomic reservation — the `refs/be-claims/*` ledger

A claim is a git ref named `refs/be-claims/<NNNN>`. GitHub's create-ref API
(`POST /repos/{owner}/{repo}/git/refs`) returns `422` if the ref already exists, which makes it a
compare-and-set: the first PR to claim a number wins, a second gets `422` and re-picks. The helper
`scripts/be_claims.sh` wrapped `list` / `claim` / `release` over this API and `git ls-remote`.

The `roadmap-id` workflow folds the claims ledger into the reserved set, allocates, then claims each
allocated id atomically. If a claim is lost (another PR raced ahead), it releases any ids it did win,
resets, and retries — the winning claim is now visible, so the next attempt steers off it. Allocation
keeps the same monotonic rule (`max(used) + 1`, skipping any reserved or claimed number).
[`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) **stays pure** — it
receives the reserved set through the environment and never talks to GitHub; the API calls and the
retry loop live in the workflow, exactly as the existing reservation does.

### Claim lifecycle

A claim earns its keep only while an *open* PR holds the id and it is *not yet on* `main`. Two
triggers in `roadmap-claims-gc.yml` enforced that:

- **Release on close** — when a roadmap PR closes (merged or not), the claims it introduced are
  dropped: a merged id now lives on `main` (the claim is redundant), an abandoned id is freed.
- **Daily sweep** — a scheduled job drops any claim whose id is on `main` or held by no open PR,
  reaping a leak from, say, a cancelled allocation run.

### Repair authority — `main`, else the lowest open-PR number

`allocate_roadmap_ids.py --repair` renumbers an item the branch *introduces* (a slug not yet on
`main`) whose number is already taken. The authority — who keeps a contested number — generalizes
from "`main` only" to:

1. `origin/main` if a *different* item already holds the number there (a merged item always wins);
2. otherwise the **lowest open-PR number** holding it, passed in via `ROADMAP_LOWER_PR_IDS` (computed
   by the workflow from `scripts/open_pr_be_map.sh`).

The branch renumbers only when it is *not* the authority. An item whose slug is already on `main` is
one the branch inherited — a rebase resolves that, never a renumber. The `roadmap-id-repair` workflow
now runs on a **daily schedule** as well as on a push to `main`, so an open-PR-only collision is
caught even when nothing merges. The sweep reserves each newly assigned id (claiming it, and folding
it into the reservations for the rest of the run) so two losers always land on distinct numbers; the
contested id's claim stays with the authority.

### Fork PRs

The bot's token cannot push to a fork's branch or create refs in this repo, so both workflows act
only on same-repo PRs (the existing `head.repo.full_name == github.repository` guard). The project's
PRs are same-repo `claude/*` / `<user>/<topic>` branches, so this is the normal path; a fork
contributor's ids are reconciled when a maintainer brings the branch in-repo.

## Alternatives considered

- **Serialize allocation with a global `concurrency` group instead of claims.** Cheaper, but
  insufficient on two counts: GitHub keeps only one *pending* run per concurrency group and cancels
  older pending ones, so three PRs opened at once can have an allocation silently dropped; and
  serialization still gives no authority for an open-PR-only collision. Atomic claims hold
  unconditionally and need no serialization.
- **Assign the real id only at merge time, keeping `BE-XXXX` through review.** Removes the race by
  serializing on `main`, but loses the stable, referenceable id during review — the repo prefixes PR
  titles with the id and reviewers cite it — and is awkward on plain GitHub, which cannot rewrite a
  merged tree without a merge queue.
- **Switch to a counter-free id scheme** (content hash, ULID, per-author ranges). Rejected: the
  roadmap is built on permanent, human-citable, monotonic `BE-NNNN` ids in the Swift-Evolution
  tradition; changing the scheme is large, irreversible churn for no reader benefit.
- **Detect collisions with a failing check and fix only by hand.** A loud red check is a weaker
  backstop that adds manual toil on every slip; auto-repair fixes it without intervention. A local
  `make roadmap-id-repair` target was also provided for deliberate, hands-on fixes.
- **Author this as a Proposal first (a `BE-XXXX` placeholder).** Unnecessary here: the work is
  implemented in the same change, so it is filed directly as Implemented under *Development
  infrastructure*, a sibling to BE-0043 — the born-implemented path the repo already uses.

## Progress

- [x] Shipped — see the *Implementing PR* above.
- [x] Reservation ledger + auto-repair retired as redundant under merge-time allocation (BE-0089):
  removed `scripts/be_claims.sh`, `scripts/open_pr_be_map.sh`, `scripts/open_pr_be_ids.sh`, the
  `roadmap-id-repair` / `roadmap-claims-gc` workflows, and the allocator's `--repair` path.

## References

- [BE-0043 — Conflict-resistant file flow](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
  — the sibling this extends from files to ids (self-healing hooks, generated indexes, merge drivers).
- [CLAUDE.md](../../CLAUDE.md) · [docs/ai-development.md](../../docs/ai-development.md) — the
  roadmap ID rules and the allocation/repair flow this hardens.
- [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) (allocation),
  [`tests/test_allocate_roadmap_ids.py`](../../tests/test_allocate_roadmap_ids.py). The claims
  ledger (`scripts/be_claims.sh`) and the open-PR tiebreaker input (`scripts/open_pr_be_map.sh`) were
  removed when the reservation layer was retired (see *Progress*).
- [`.github/workflows/roadmap-id.yml`](../../.github/workflows/roadmap-id.yml) — the merge-time
  allocator. The `roadmap-id-repair` and `roadmap-claims-gc` workflows were removed with the
  reservation layer.

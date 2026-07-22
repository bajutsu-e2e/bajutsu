**English** · [日本語](BE-0089-merge-time-be-id-allocation-ja.md)

# BE-0089 — Merge-time BE-ID allocation on main

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0089](BE-0089-merge-time-be-id-allocation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0089") |
| Implementing PR | [#359](https://github.com/bajutsu-e2e/bajutsu/pull/359), [#436](https://github.com/bajutsu-e2e/bajutsu/pull/436) (retired the superseded claims/repair machinery) |
| Topic | Development infrastructure (contributor workflow) |
<!-- /BE-METADATA -->

## Introduction

A roadmap item's permanent `BE-NNNN` id is assigned the moment its pull request opens: the
[`roadmap-id`](../../.github/workflows/roadmap-id.yml) workflow runs on `pull_request`,
allocates the next free number, claims it atomically as a `refs/be-claims/*` ref, pushes the rename
back onto the branch, and rewrites the PR title's `BE-XXXX` to the allocated id.
[BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
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

- **Rejected PRs free their claim.** When a roadmap PR closed — merged or not — the (since-retired)
  `roadmap-claims-gc` workflow released the `refs/be-claims/*` it introduced, and the branch rename
  never reached `main`, so a rejection alone leaves no row on `main`.
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

The allocation logic ([`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py))
is reused **unchanged**: it already finds every `BE-XXXX-<slug>/` placeholder in the working tree,
allocates `max(used) + 1` per item (sorted by slug for determinism), `git mv`s the directory and
files, rewrites the in-file token, and fixes the index rows. What changes is *where* and *when* it
runs — against `main`, after a merge — plus the workflow plumbing around it.

### Flow

1. The `ideation` skill authors the item as `BE-XXXX-<slug>` (unchanged). The PR opens with a plain
   scoped title (e.g. `docs(roadmap): …`) and **no** `[BE-NNNN]` prefix.
2. The reviewer reviews and approves the `BE-XXXX` content. **No allocation happens on the branch.**
3. Auto-merge (or the merge queue) merges the branch **as-is**, `BE-XXXX` intact. No commit is pushed
   to the branch after approval, so no approval is dismissed.
4. The merge is a push to `main`. A `roadmap-id` job triggered by `push: main` runs the allocator
   against `main`, commits the rename + regenerated index directly to `main`, and posts a comment on
   the merged PR announcing the allocated `BE-NNNN` (with a link to the item).

The PR title is **not** rewritten, and a BE-creation PR carries **no** `[BE-NNNN]` prefix at all. The
real number is never known on the branch (it is allocated only after the merge), so a placeholder
`[BE-XXXX]` prefix would carry no information and rewriting it post-merge would be churn for no
functional gain; a bot comment is the natural, durable place to record the allocated id and link it to
the merged PR. (The current `[BE-NNNN]`-prefix rule applies to PRs that *implement* an
already-numbered item, where the id is known up front — see *References*.)

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
  pull-requests: write        # comment the allocated id on the merged PR
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

### Setting up the GitHub App

The bypass identity is a dedicated GitHub App, created once by a maintainer with admin rights:

1. **Create the App** (org- or repo-owned). It needs no webhook and no callback URL — it is used
   only to mint an installation token in CI. Repository permissions are **Contents: Read and write**
   (to push the renumber commit) and **Pull requests: Read and write** (to comment the allocated id),
   and nothing else.
2. **Install it on this repo only**, so its reach is a single repository.
3. **Add the App to `main`'s ruleset bypass list** — it is the only entry — so its installation token
   can push the renumber commit (or merge the renumber PR) past branch protection.
4. **Generate a private key** and store it with the App id as Actions secrets, scoped via an
   Environment to the `main` ref so no PR-triggered job can read them.

The workflow mints a short-lived installation token from those secrets and uses it for checkout,
push, and `gh`:

```yaml
    - uses: actions/create-github-app-token@<sha>   # pin to a full commit SHA
      id: app-token
      with:
        app-id: ${{ secrets.AUTOMATION_BOT_APP_ID }}
        private-key: ${{ secrets.AUTOMATION_BOT_PRIVATE_KEY }}
    - uses: actions/checkout@<sha>
      with:
        token: ${{ steps.app-token.outputs.token }}
```

The token expires in about an hour and is unattached to any person; commits the App makes through
the API are verified/signed and attributed to it, so every bypass push is auditable (see *Securing
the bypass identity*).

### Feasibility

Each load-bearing assumption, made concrete:

- **A transient `BE-XXXX` on `main` keeps the gate green.** All three roadmap tools key on
  `^BE-(\d{4})-` and skip anything else: [`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py)
  and [`tests/test_roadmap_index.py`](../../tests/test_roadmap_index.py) (through
  [`build_roadmap_index.py`](../../scripts/build_roadmap_index.py), whose `load_items` `continue`s
  on a non-numbered dir) and [`promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py).
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
- **Landing the renumber on protected `main` needs a bypass actor — the design's load-bearing
  prerequisite.** Today the bot pushes renames to *PR branches*; here the renumber commit must land on
  `main`, which on most repos is protected (no direct push; PRs require review). There are two ways to
  land it, and **both need the same grant**: a **direct push** by a token on `main`'s ruleset
  **bypass list** (a GitHub App installation token or a PAT), or a **bot renumber PR with auto-merge**
  — but auto-merge does *not* waive required approvals, so that PR, too, needs the bot to bypass (or
  otherwise satisfy) the review requirement. Adding a maintenance bot as a bypass actor is a standard
  pattern, and the default `GITHUB_TOKEN` is typically blocked by `main`'s protection, so a
  bypass-capable identity is required either way. **If org policy forbids *any* bypass on `main`, this
  merge-time-on-`main` design is infeasible** and the approval-time fallback under *Alternatives*
  (which never pushes to `main`) applies instead.
- **Commenting on the merged PR is best-effort.** A `push` event carries no PR number, so the job
  resolves it from the merge commit — `gh api repos/{owner}/{repo}/commits/${SHA}/pulls` — then
  `gh pr comment <pr> --body "Allocated **BE-NNNN** — <link to the item on main>"`. The comment is
  informational, so a miss (e.g. a merge commit that maps to no PR) is harmless and never blocks the
  allocation commit, which has already landed on `main`.

### Securing the bypass identity

A token that can bypass `main`'s protection is a high-value credential, so the design keeps its power
structurally small rather than trusting the secret alone:

- **The privileged job only ever runs reviewed code, post-merge.** It triggers on `push: main`, which
  fires only *after* a merge that already cleared review and required checks, and it checks out
  **`main`** — running `allocate_roadmap_ids.py` / `build_roadmap_index.py` as they exist on `main`,
  never a PR branch's copy. It never checks out untrusted PR-head code under the privileged token (the
  classic `pull_request_target` escalation), so no attacker-supplied code runs in the job.
- **The output is bounded and verified.** The legitimate push is always the same narrow mechanical
  diff — a `BE-XXXX` → `BE-NNNN` rename plus the regenerated index, under `roadmaps/**` only. A guard
  re-runs the allocator in check mode (or diffs the pushed commit) and fails if the bypass commit
  touches anything outside `roadmaps/**` or deviates from the expected rename, capping the blast radius
  of any misuse to that shape.
- **A scoped GitHub App, not a PAT.** The bypass identity is a dedicated App (set up under *Setting
  up the GitHub App*), not a personal PAT: its installation token is short-lived (≈1 h), unattached
  to a person, and limited to `contents: write` + `pull-requests: write` on this one repo, so the
  bypass grant carries the least privilege that does the job.
- **Supply-chain discipline in the privileged job.** Pin every third-party action to a full commit
  SHA (the repo's existing rule) and run no dependency install — the allocate scripts are stdlib-only
  Python — so no third-party code executes alongside the token.
- **Auditable, signed commits.** Commits the App makes through the API are verified/signed and
  attributed to the App, so every bypass push is visible in history and the audit log; an App push
  that does not match the renumber pattern is a detectable anomaly.
- **The no-bypass fallback removes the credential entirely.** Where even a scoped App bypass is
  unacceptable, the approval-time-on-branch fallback (see *Alternatives considered*) introduces no
  `main`-bypass identity at all — the strongest mitigation, at the cost of the
  stale-approval-dismissal setting.

### Interaction with BE-0061's machinery

Allocating on `main` **serializes** allocation on a single branch, which makes the same-window race
BE-0061 closed largely moot: at most one allocate run touches `main` at a time, and it always reads
the latest `main`. The atomic `refs/be-claims/*` ledger, `roadmap-id-repair`, and `roadmap-claims-gc`
are therefore redundant under this model. They were initially kept in place as defense in depth; once
merge-time allocation had proven out they were **retired** — the ledger, both workflows, their
supporting scripts, and the allocator's `--repair` path were removed, leaving only the pure allocator
and the merge-time `roadmap-id` workflow (see
[BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)'s
*Progress*).

### Prime-directive compliance

Contributor-workflow infrastructure only. No LLM is added to any path; `run` and CI stay
deterministic; nothing app-specific moves into the tool, drivers, or runner. It sits in the same
family as
[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md),
BE-0061,
[BE-0074](../BE-0074-be-template-standardization/BE-0074-be-template-standardization.md),
and [BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md).

## Alternatives considered

- **Allocate at approval by pushing a rename to the branch, then auto-merge — the no-`main`-push
  fallback.** This never pushes to `main`: the rename lands on the *PR branch*, and the merge into
  `main` still goes through the normal protected-PR / auto-merge path. So it is the design to use
  **when the bot cannot be a bypass actor on `main`** (regime where merge-time-on-`main` is
  infeasible). Its cost is branch protection's "dismiss stale pull request approvals when new commits
  are pushed": the post-approval rename commit would dismiss the approval and stall auto-merge. That
  is avoided by turning off *that one setting* (it is independent of "require a PR" / "require
  approvals"), or — less cleanly — by a bot re-approval GitHub does not reliably count. Rejected as
  the *primary* design only because merge-time-on-`main`, where a bypass actor is available, is
  gap-free with no protection-setting trade-off; this is the proper fallback otherwise.
- **Renumber via a bot PR instead of a direct push to `main`.** Tempting as a way to avoid a direct
  push, but it is *not* a way to avoid the bypass requirement: a renumber PR must still merge into
  protected `main`, and auto-merge does not waive required reviews, so the bot must bypass (or satisfy)
  them anyway. It only adds a second PR and a longer `BE-XXXX` window for no reduction in the
  permission it needs. Kept only as a stylistic option where a visible PR trail for the renumber is
  wanted.
- **Keep PR-open allocation; switch `max + 1` to smallest-free.** Far lower churn — no trigger change,
  no `main` push — and it would fill the hole a rejected lower-numbered PR leaves. Rejected as the
  primary path because reusing a number whose `[BE-00xx]` already appeared in a rejected PR's title
  and review thread makes that id *ambiguous* in project history, the very thing the "never reuse a
  number" rule prevents. A reasonable lighter-weight fallback.
- **Accept gaps as harmless; document and stop.** Cheapest: ids are permanent and monotonic by
  design, and Swift-Evolution tolerates gaps. Rejected against the stated goal of keeping `main`'s
  numbering contiguous by construction.
- **Leave BE-0061's decision untouched (do nothing).** Rejected: the spent-id and non-contiguous
  concern is real, if minor, and is removable without reopening any of BE-0061's race guarantees.

## Progress

- [x] Shipped — see the *Implementing PR* above.
- **Historical note:** the illustrative workflow snippet above still shows a
  `python3 scripts/build_roadmap_index.py  # add the now-numbered rows` step. That call was retired
  by [#1257](https://github.com/bajutsu-e2e/bajutsu/pull/1257) along with the generated `README.md` /
  `README-ja.md` index tables it used to update — the `roadmap-id` workflow no longer runs it, since
  there is nothing left for it to add a row to. The allocation mechanism this item describes
  (claim, rename, push) is otherwise unaffected.

## References

- [BE-0061 — Collision-proof BE-ID allocation](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
  — the item this extends; its *Alternatives considered* records the "assign id at merge time" option
  this realizes, and its hardening (atomic claims, repair, claims-gc) was later retired once this
  model proved out (see its *Progress*).
- [`.github/workflows/roadmap-id.yml`](../../.github/workflows/roadmap-id.yml) (the trigger this
  moves from `pull_request` to `push: main`). The `roadmap-id-repair` and `roadmap-claims-gc`
  workflows were in scope too and have since been removed.
- [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) (reused unchanged),
  [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py),
  [`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py) — the three tools
  that all skip a `BE-XXXX` directory, which is what keeps `main` green during the transient window.
- [`actions/create-github-app-token`](https://github.com/actions/create-github-app-token) — mints the
  short-lived installation token for the bypass App in the workflow; pinned to a full commit SHA like
  every other third-party action.
- [`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py),
  [`tests/test_roadmap_index.py`](../../tests/test_roadmap_index.py) — the gate tests that key on
  `^BE-(\d{4})-` and so ignore the placeholder.
- [`CLAUDE.md`](../../CLAUDE.md) ·
  [`roadmaps/README.md`](../README.md) ·
  [`docs/ai-development.md`](../../docs/ai-development.md) — the authoring rules updated to say the
  number is allocated on `main` after the merge, not at PR-open, and that a BE-creation PR's title
  carries no `[BE-NNNN]` prefix (the prefix rule stays for PRs implementing an already-numbered item).
- GitHub docs — *Automatically merging a pull request* (auto-merge) and *Managing a merge queue* — the
  native mechanisms that merge the `BE-XXXX` branch as-is.

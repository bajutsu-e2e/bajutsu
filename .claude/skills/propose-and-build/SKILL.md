---
name: propose-and-build
model: opus
description: >-
  Author a BE (Bajutsu Evolution) proposal and its implementation in parallel, landing them
  as a temporary two-PR stack — the proposal PR first, the implementation PR second. Use when
  the author is confident in a small, well-scoped feature and wants to "propose and build at
  once", "write the BE and the code together", or "stack the implementation on the proposal"
  instead of waiting for the serial ideation → merge → allocate → implement-be path. Composes
  the ideation skill (Phase A, authoring only) and the implement-be skill (Phase B + hand-off)
  around an explicit hand-off: once the proposal merges and CI allocates the real BE-NNNN, the
  implementation branch rebases, rewrites its BE-XXXX references, retargets to main, and runs
  implement-be's promotion + gate steps. Falls back to the serial path when a design is
  genuinely uncertain. Scope spans authoring and product code — the counterpart skills stay
  single-purpose (ideation never implements; implement-be needs a numbered item first).
---

# propose-and-build

Author a roadmap (BE) item **and** its implementation at the same time, then land them as a
**temporary two-PR stack** — the proposal PR first, the implementation PR second. You are the
author *and* the implementer; the deterministic gate (`make check`) is the judge, never an LLM.
Converse in the user's language; write code, commits, and PR text per the conventions the two
skills below already define.

This is the third skill in the roadmap triangle, and it **composes** the other two rather than
restating them:

- [`ideation`](../ideation/SKILL.md) — authors a proposal and stops at the roadmap files
  (never touches product code).
- [`implement-be`](../implement-be/SKILL.md) — ships an *already-numbered* item from its id.
- **`propose-and-build`** (this skill) — does both, stacked, for a small item the author is
  ready to build now.

Reach for it only when the serial `ideation` → `implement-be` path's latency is pure overhead
— see [When to use it](#when-to-use-it-and-when-not-to) before starting.

## Why a stack, and why it is temporary

A permanent `BE-NNNN` id is allocated only **after** the proposal PR merges to `main`
([BE-0089](../../../roadmaps/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)).
So the implementation cannot honour the two `implement-be` steps that *depend* on a real id —
flipping the item to `Status: Implemented` with an `Implementing PR` row, and prefixing the PR
title `[BE-NNNN]` — until the proposal has landed. This skill does not change that timing; it
defines how the implementation branch **adopts** the id afterward.

```
                 (author is confident in a small, scoped feature)
                                    │
      ┌─────────────────────────────┴─────────────────────────────┐
      ▼                                                             ▼
  PR #1  BE proposal                                     PR #2  implementation
  branch: claude/<topic>                                 branch: claude/<topic>-impl
  base:   main                                           base:   claude/<topic>   (stacked)
  BE-XXXX-<slug>/ @ Status: Proposal                     code + tests against the proposal spec
  plain scoped title, NO [BE-…] prefix                   DRAFT, NO Status flip, NO [BE-…] prefix
      │                                                             │
      │  reviewed & merged as-is (proposal intact)                  │  kept in draft, rebased as #1 evolves
      ▼                                                             │
  roadmap-id allocates BE-NNNN on main (BE-0089)                    │
      │                                                             │
      └──────────────────────── HAND-OFF ──────────────────────────┘
                                    │
                                    ▼
   #2: rebase onto origin/main · rewrite BE-XXXX→BE-NNNN in the diff · retarget base→main
       · run implement-be steps 8–10 (flip Status, Implementing PR row, gate, [BE-NNNN] title)
```

The stack exists **only while PR #1 is open**. Once the proposal merges and the id is
allocated, PR #2 is rebased onto `main` and retargeted, becoming an ordinary
`implement-be`-shaped PR. That is what keeps this compatible with BE-0089: nothing changes
about *when* the number is allocated.

## Prime directives (unchanged — they bind Phase B exactly as they bind `implement-be`)

Re-read [`CLAUDE.md`](../../../CLAUDE.md) and [`DESIGN.md`](../../../DESIGN.md) before you
touch code. Phase A is authoring-only (`ideation`'s rules); product code appears only in
Phase B, on the separate implementation branch, under `implement-be`'s rules:

1. **AI authors and investigates, never judges.** No LLM call on the Tier‑2 `run`/CI gate.
2. **Determinism first.** No fixed `sleep` (condition waits only); an ambiguous selector
   fails immediately rather than tapping the first match.
3. **App-agnostic.** Per-app differences live in config (`apps.<name>`); the tool, drivers,
   and runner stay unchanged across apps.

This skill adds **no** automation to CI — it is a human/agent procedure only, so it introduces
no new machinery on any gate.

## When to use it (and when not to)

Use it only for a **small, well-scoped item whose design the author does not expect to change
materially in review**. The parallelism buys you the dead time between "proposal opened" and
"id allocated"; it costs you rework on the implementation branch if review reshapes the
proposal.

- **Good fit** — a contained feature the author already holds fully formed: a new `make`
  target, a self-contained skill, a small deterministic check, a docs restructure with a
  little supporting code.
- **Fall back to the serial path** ([`ideation`](../ideation/SKILL.md) →
  [`implement-be`](../implement-be/SKILL.md)) when the design is genuinely uncertain, wide-
  reaching, or likely to be reshaped by review. The rework the implementation branch would eat
  is the accepted trade-off, and it is not worth paying when the proposal is not yet settled.

If in doubt, prefer the serial path — it is the default for a reason.

## Workflow

### Phase A — author the proposal (delegates to `ideation`)

Author the BE proposal exactly as [`ideation`](../ideation/SKILL.md) prescribes — this skill
does not restate those rules, it runs them:

- Branch `claude/<topic>` off the latest `origin/main`
  (`git fetch origin && git switch -c claude/<topic> origin/main`).
- Scaffold `roadmaps/BE-XXXX-<slug>/` with `make new-roadmap-item` at `Status: Proposal`,
  fill the `TBD` sections under the [`document-writing`](../document-writing/SKILL.md) skill (the
  authoritative prose norm for both languages, invoked *before* drafting), and localize the
  Japanese side under the [`japanese-document-writing`](../japanese-document-writing/SKILL.md) skill
  (敬体; the Japanese layer beneath `document-writing`, natural Japanese, not a literal rendering).
  Keep the `BE-XXXX` placeholder — the real id is allocated on `main`
  by CI ([`roadmap-id`](../../../.github/workflows/roadmap-id.yml)), never guessed.
- Self-review the staged diff against the CI review contract (`ideation` step 5): a fresh
  subagent, blind to the authoring conversation, applies
  [`.github/claude-review-prompt.md`](../../../.github/claude-review-prompt.md) — the same
  contract the "Claude review" GitHub Actions workflow uses. Fix every finding, except a false
  positive or an already-explained trade-off (noted and left as-is) or one that calls for a
  genuine design change (escalated to the user instead); capped at 3 rounds.
- Run `make check` (roadmap changes are docs-only, but the gate is the contract), then open
  **PR #1** with a plain scoped title (`docs(roadmap): …`) and **no** `[BE-…]` prefix — a
  BE-creation branch never encodes an id, so [`scripts/lint_pr.py`](../../../scripts/lint_pr.py)
  neither requires nor rejects one here.

### Phase B — implement against the placeholder (delegates to `implement-be` steps 3–7)

Create the implementation branch **stacked on** the proposal branch, so the two can proceed in
parallel:

```bash
git switch -c claude/<topic>-impl claude/<topic>
```

Or isolate it in its own workspace with `make worktree TOPIC=<topic>-impl` when you want the
two checkouts fully separate. Then treat the `BE-XXXX-<slug>/` proposal as the spec and run
`implement-be`'s steps 3–7 — ground yourself in the code, plan and confirm before writing,
implement with tests, and review the diff with the `simplify` / `code-review` skills — **with
two carve-outs that depend on the not-yet-allocated id**:

- **Do not** flip the item's `Status` to `Implemented`, and **do not** add the
  `Implementing PR` row. There is no id and no merged proposal to promote yet.
- **Do not** add the `[BE-NNNN]` title prefix to PR #2.

Open **PR #2 as a draft** with `base: claude/<topic>` (`gh pr create --draft --base
claude/<topic>`). Keep it in draft and rebase it as PR #1 evolves in review.

> **The tracking issue does not exist yet.** `implement-be` step 2 self-assigns the item's
> `roadmap-tracking` issue, but that issue is created by the
> [BE-0109](../../../roadmaps/BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md)
> sync only after the numbered item exists on `main`. During Phase B there is nothing to claim
> — defer the self-assign to the hand-off below.

### Hand-off — the load-bearing step

Triggered by **PR #1 merging and `roadmap-id` allocating `BE-NNNN` on `main`**. On the
implementation branch, in order:

1. **Rebase onto the merged proposal.**
   ```bash
   git fetch origin && git rebase origin/main
   ```
   This pulls in the merged proposal *and* the renumber commit. The `BE-XXXX-<slug>/` →
   `BE-NNNN-<slug>/` directory rename is a git rename; conflicts are mechanical.
2. **Rewrite the stale placeholder inside your own diff.** Rewrite every `BE-XXXX` reference
   that PR #2 *introduced* (doc cross-refs, test data, comments) to the allocated `BE-NNNN`.
   The proposal's own files were already renumbered on `main` by the workflow — this step only
   fixes references the *implementation* added.
3. **Retarget the PR base to `main`.**
   ```bash
   gh pr edit --base main
   ```
   GitHub auto-retargets a stacked PR when its base branch is deleted on merge, but do this
   explicitly so the flow does not depend on the base branch being deleted.
4. **Now that `BE-NNNN` exists, run the id-dependent `implement-be` steps:**
   - Self-assign the tracking issue (`implement-be` step 2 — it exists now).
   - Run `implement-be` steps 8–10: flip `Status: Implemented` + add the `Implementing PR`
     row in **both** language files, ticking the `Progress` boxes; `make check`; mark PR #2
     ready (`gh pr ready`); and add the `[BE-NNNN]` title prefix so
     [`pr-title`](../../../.github/workflows/pr-title.yml) is satisfied once the branch encodes
     the id.

## Machine-checkable outcome

The skill is a procedure, so the deterministic outcome is the existing gate applied to whatever
PR #2 produces: `make check` green after the hand-off, the roadmap dashboard reflecting
`Status: Implemented`, and — enforced by CI's `pr-title` check once the post-hand-off branch
encodes `BE-NNNN` — the matching `[BE-NNNN]` title prefix. No new checker is added; correctness
reduces to the already-gated `implement-be` end state plus the ordering discipline above.

## References

- [`ideation`](../ideation/SKILL.md) — the upstream skill this composes for Phase A (authoring
  only, never implements).
- [`implement-be`](../implement-be/SKILL.md) — the skill this composes for Phase B and the
  hand-off (steps 3–10); the deterministic counterpart that ships a numbered item.
- [`CLAUDE.md`](../../../CLAUDE.md) · [`DESIGN.md`](../../../DESIGN.md) — the prime directives
  every change must honor.
- [`docs/ai-development.md`](../../../docs/ai-development.md) — the parallel-work rules and the
  three-skill triangle (`ideation` / `implement-be` / `propose-and-build`) this fits into.
- [BE-0089 — Merge-time BE-ID allocation on main](../../../roadmaps/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)
  — the constraint that makes the stack necessarily *temporary*: the real id exists only after
  the proposal merges, so the hand-off is where the implementation branch adopts it.
- [BE-0109 — Roadmap tracking issues](../../../roadmaps/BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md)
  — the tracking issue `implement-be` self-assigns; it does not exist during Phase B.
- [`scripts/lint_pr.py`](../../../scripts/lint_pr.py) · [`.github/workflows/pr-title.yml`](../../../.github/workflows/pr-title.yml)
  — the CI check that enforces the `[BE-NNNN]` prefix once a branch encodes a real id.

---
name: propose-and-build
model: opus
description: >-
  Author a BE (Bajutsu Evolution) proposal and its implementation together in a single PR — a
  BE-creation PR that carries the roadmap item, the code, and the tests at once. Use when the
  author is confident in a small, well-scoped feature and wants to "propose and build at once",
  "write the BE and the code together", or land a proposal and its implementation in one review
  instead of waiting for the serial ideation → merge → allocate → implement-be path. Composes the
  ideation skill (Phase A, authoring only) and the implement-be skill (Phase B) in one branch: the
  item keeps the BE-XXXX placeholder and reaches Status: Implemented in the PR, and CI allocates
  the real BE-NNNN when the PR merges (BE-0089), rewriting the placeholder inside the item's own
  files. The one invariant is that the placeholder id never appears outside those files. Falls back
  to the serial path when a design is genuinely uncertain. Scope spans authoring and product code —
  the counterpart skills stay single-purpose (ideation never implements; implement-be needs a
  numbered item first).
---

# propose-and-build

Author a roadmap (BE) item **and** its implementation at the same time, then land them in a
**single PR** — a BE-creation PR that carries the roadmap item, the code, and the tests together.
You are the author *and* the implementer; the deterministic gate (`make check`) is the judge, never
an LLM. Converse in the user's language; write code, commits, and PR text per the conventions the
two skills below already define.

This is the third skill in the roadmap triangle, and it **composes** the other two rather than
restating them:

- [`ideation`](../ideation/SKILL.md) — authors a proposal and stops at the roadmap files
  (never touches product code).
- [`implement-be`](../implement-be/SKILL.md) — ships an *already-numbered* item from its id.
- **`propose-and-build`** (this skill) — does both, in one PR, for a small item the author is
  ready to build now.

Reach for it only when the serial `ideation` → `implement-be` path's latency is pure overhead
— see [When to use it](#when-to-use-it-and-when-not-to) before starting.

## Why one PR, and how the id arrives

A permanent `BE-NNNN` id is allocated only **after** a BE-creation PR merges to `main`
([BE-0089](../../../roadmaps/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)):
the [`roadmap-id`](../../../.github/workflows/roadmap-id.yml) workflow renames the placeholder
directory and rewrites `BE-XXXX` → `BE-NNNN` **inside the item's own files**
([`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py)). That single fact
shapes everything this skill does, because two things follow from it.

First, the id-dependent finishing touches an `implement-be` PR normally carries do not need a real
number here. The item reaches `Status: Implemented` with an `Implementing PR` row **while it still
holds the `BE-XXXX` placeholder** — `Status` and the PR number are independent of the id — and the
allocator rewrites the placeholder to the allocated number on merge, so the item lands correctly
numbered and Implemented with no post-merge fixup. The PR title stays a plain scoped title with
**no** `[BE-NNNN]` prefix, exactly as every BE-creation PR does: the prefix rule applies only to a
PR that implements an *already-numbered* item, so [`scripts/lint_pr.py`](../../../scripts/lint_pr.py)
neither requires nor rejects one, and [`pr-title`](../../../.github/workflows/pr-title.yml) is
satisfied.

Second, the allocator touches **only the item's own directory** — it does not sweep the repository.
So the one invariant this skill imposes is that the `BE-XXXX` placeholder must never appear anywhere
but the item's own files (see [The one invariant](#the-one-invariant)). Any `BE-XXXX` the
implementation writes into code, tests, comments, or a doc outside `roadmaps/BE-XXXX-<slug>/` would
survive the merge as a stale reference on `main`.

```
                 (author is confident in a small, scoped feature)
                                    │
                                    ▼
   one branch: claude/<topic>   base: main
     roadmaps/BE-XXXX-<slug>/  @ Status: Implemented   (placeholder id, Implementing PR row)
     code + tests against the proposal spec            (BE-XXXX only inside the item's own files)
     plain scoped title, NO [BE-…] prefix
                                    │
                                    │  reviewed as one PR, then merged by a human
                                    ▼
   roadmap-id allocates BE-NNNN on main (BE-0089)
     · renames BE-XXXX-<slug>/ → BE-NNNN-<slug>/
     · rewrites BE-XXXX → BE-NNNN inside the item's own files
                                    │
                                    ▼
   item lands numbered and Implemented — no post-merge fixup
```

## Prime directives (unchanged — they bind Phase B exactly as they bind `implement-be`)

Re-read [`CLAUDE.md`](../../../CLAUDE.md) and [`DESIGN.md`](../../../DESIGN.md) before you
touch code. Phase A is authoring-only (`ideation`'s rules); product code appears only in
Phase B, under `implement-be`'s rules:

1. **AI authors and investigates, never judges.** No LLM call on the Tier‑2 `run`/CI gate.
2. **Determinism first.** No fixed `sleep` (condition waits only); an ambiguous selector
   fails immediately rather than tapping the first match.
3. **App-agnostic.** Per-app differences live in config (`targets.<name>`); the tool, drivers,
   and runner stay unchanged across targets.

This skill adds **no** automation to CI — it is a human/agent procedure only, so it introduces
no new machinery on any gate.

## When to use it (and when not to)

Use it only for a **small, well-scoped item whose design the author does not expect to change
materially in review**. One PR fuses the design checkpoint with code review: merging it accepts the
proposal and the implementation in a single act. That is honest for a settled design, but it removes
the serial path's separate proposal checkpoint — so the trade-off is only worth it when the design
is genuinely fixed.

- **Good fit** — a contained feature the author already holds fully formed: a new `make`
  target, a self-contained skill, a small deterministic check, a docs restructure with a
  little supporting code.
- **Fall back to the serial path** ([`ideation`](../ideation/SKILL.md) →
  [`implement-be`](../implement-be/SKILL.md)) when the design is genuinely uncertain, wide-
  reaching, or likely to be reshaped by review. Reworking the implementation against a proposal
  that review reshapes is the accepted cost of parallelism, and it is not worth paying when the
  proposal is not yet settled.

If in doubt, prefer the serial path — it is the default for a reason.

## Workflow

Everything happens on **one branch**, `claude/<topic>`, cut off the latest `origin/main`
(`git fetch origin && git switch -c claude/<topic> origin/main`).

### Phase A — author the proposal (delegates to `ideation`)

Author the BE proposal exactly as [`ideation`](../ideation/SKILL.md) prescribes — this skill
does not restate those rules, it runs them:

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

### Phase B — implement against the placeholder (delegates to `implement-be` steps 3–9)

On the **same branch**, treat the `BE-XXXX-<slug>/` proposal as the spec and run `implement-be`'s
steps 3–7 — ground yourself in the code, plan and confirm before writing, implement with tests, and
review the diff with the `simplify` / `code-review` skills. Then, unlike the serial `implement-be`,
run its promotion steps 8–9 **now**, because `Status` and the PR number do not depend on the
not-yet-allocated id:

- Flip the item's `Status` to `Implemented` and tick the `Progress` boxes in **both** language
  files, keeping the `BE-XXXX` placeholder — the allocator rewrites it on merge.
- Add the `Implementing PR` row (in both languages) referencing this PR's own number, right after
  the `Tracking issue` row, once the PR exists.

Two `implement-be` steps stay deferred because they need a number or an issue that exists only after
merge:

- **No `[BE-NNNN]` title prefix.** The PR keeps a plain scoped title; it is a BE-creation PR, and
  the id does not exist until it merges.
- **No tracking-issue self-assign.** `implement-be` step 2 self-assigns the item's
  `roadmap-tracking` issue, but that issue is created by the
  [BE-0109](../../../roadmaps/BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md)
  sync only after the numbered item exists on `main` — after this PR has merged and the work is
  already done. There is nothing to claim during Phase B.

Run `make check` (green is the contract) and open the PR. Because this is a BE-creation PR, its id
is allocated only when a human merges it, so — like an `ideation` proposal — **do not auto-create
it: push the branch and let the human open the PR** (BE-0230). The human opens it as a Draft (it
carries product code); keep pushing fixes until `make check` and CI are both green before it is
marked ready.

## The one invariant

The allocator rewrites `BE-XXXX` → `BE-NNNN` **only inside the item's own directory**
(`roadmaps/BE-XXXX-<slug>/`). So the placeholder id must appear **nowhere else**. Do not write
`BE-XXXX` into implementation code, test data, comments, or any doc outside the item's own files:
such a reference is not rewritten on merge and lands stale on `main`, and CI will not catch it (the
roadmap format check walks only `roadmaps/`). Refer to the feature by name in code, and confine the
id to the roadmap item, which is the one place the allocator fixes.

## Merge must trigger allocation

The `roadmap-id` workflow fires on a push to `main`, but a merge performed with the default
`GITHUB_TOKEN` (native auto-merge) does not re-trigger `push`-driven workflows, so allocation would
never run and the PR would land with `BE-XXXX` — code and all — unallocated on `main`. Merge this PR
in a way that fires `roadmap-id`: a manual merge, or auto-merge configured with the roadmap App
token. This is the same constraint an `ideation` proposal already lives under; it only matters more
here because the implementation lands with the item.

## Machine-checkable outcome

The skill is a procedure, so the deterministic outcome is the existing gate applied to whatever the
PR produces: `make check` green, the roadmap dashboard reflecting `Status: Implemented` once the id
is allocated, and — per [`pr-title`](../../../.github/workflows/pr-title.yml) — the plain scoped
title a BE-creation branch is required to keep. No new checker is added; correctness reduces to the
already-gated `implement-be` end state plus the id-confinement invariant above.

## References

- [`ideation`](../ideation/SKILL.md) — the upstream skill this composes for Phase A (authoring
  only, never implements).
- [`implement-be`](../implement-be/SKILL.md) — the skill this composes for Phase B (steps 3–9); the
  deterministic counterpart that ships a numbered item.
- [`CLAUDE.md`](../../../CLAUDE.md) · [`DESIGN.md`](../../../DESIGN.md) — the prime directives
  every change must honor.
- [`docs/ai-development.md`](../../../docs/ai-development.md) — the parallel-work rules and the
  three-skill triangle (`ideation` / `implement-be` / `propose-and-build`) this fits into.
- [BE-0089 — Merge-time BE-ID allocation on main](../../../roadmaps/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)
  — the constraint that lets one PR work: the real id exists only after the PR merges, and the
  allocator supplies it by rewriting the placeholder inside the item's own files.
- [BE-0109 — Roadmap tracking issues](../../../roadmaps/BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md)
  — the tracking issue `implement-be` self-assigns; it does not exist during Phase B.
- [`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py) — the allocator
  whose item-only rewrite scope is the reason for [The one invariant](#the-one-invariant).
- [`scripts/lint_pr.py`](../../../scripts/lint_pr.py) · [`.github/workflows/pr-title.yml`](../../../.github/workflows/pr-title.yml)
  — the CI check that keeps a BE-creation branch on a plain scoped title.

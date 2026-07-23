**English** · [日本語](BE-0216-propose-and-build-parallel-skill-ja.md)

# BE-0216 — propose-and-build: author a BE proposal and its implementation together in one PR

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0216](BE-0216-propose-and-build-parallel-skill.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0216") |
| Implementing PR | [#848](https://github.com/bajutsu-e2e/bajutsu/pull/848), [#1304](https://github.com/bajutsu-e2e/bajutsu/pull/1304) |
| Topic | Development infrastructure (contributor workflow) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's roadmap workflow is deliberately two-staged: the [`ideation`](../../.claude/skills/ideation/SKILL.md)
skill *authors* a BE (Bajutsu Evolution) proposal and stops at the roadmap files, and the
[`implement-be`](../../.claude/skills/implement-be/SKILL.md) skill *ships* an already-numbered item
from its id. The two are strictly serial by construction: a permanent `BE-NNNN` id is allocated only
**after** the proposal PR merges to `main` ([BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)),
so implementation cannot start under a real id until the proposal has landed.

This item adds a **third** skill, `propose-and-build`, for the case where the author is confident
enough in a small, well-scoped feature to write the proposal *and* its implementation at the same
time, then land them in a **single BE-creation PR** that carries the roadmap item, the code, and the
tests together. It does not replace `ideation` / `implement-be`, and it does not change BE-0089's
merge-time allocation: the item keeps the `BE-XXXX` placeholder throughout the PR's life, and CI
allocates the real id the moment the PR merges, exactly as for any proposal. The serial path remains
the default for anything whose design deserves review before code is written.

## Motivation

### The serial path has real latency, and for small items that latency is pure overhead

The `ideation` → merge → allocate → `implement-be` chain is the right default: it forces a design to
clear review before anyone writes code against it, and it keeps the `BE-NNNN` sequence contiguous by
only spending a number on an item that actually ships (BE-0089). But it serializes two activities
that, for a small and low-risk feature, the same author often already holds fully formed in their
head. For such an item the author must:

1. run `ideation`, open the proposal PR, and **wait** for it to merge;
2. wait for the `roadmap-id` workflow to allocate `BE-NNNN` on `main`;
3. only then run `implement-be BE-NNNN`, re-establishing all the context they had at step 1.

The wait between (1) and (3) is dead time for a change the author is ready to build now, and step 3
pays a context-reacquisition cost — re-reading the proposal, the touch-points, the surrounding code —
that was already paid during ideation. Nothing in the prime directives requires this serialization;
it falls out of the id-allocation timing, not out of any correctness need.

### Merge-time allocation shapes the single PR; it does not block it

The obvious fix — "write the code alongside the proposal and land both together" — looks as though it
collides with BE-0089, because until the PR merges the item has no real id. It does not, once two
facts are separated. First, the two finishing touches an `implement-be` PR normally carries do **not**
both need the number. Flipping the item to `Status: Implemented` and recording an `Implementing PR`
row depend only on the status and the PR number, not on the id, so the item can reach
`Status: Implemented` **while it still holds the `BE-XXXX` placeholder**; the `roadmap-id` workflow
rewrites the placeholder to the allocated `BE-NNNN` on merge, and the item lands numbered and
Implemented with no post-merge fixup. The `[BE-NNNN]` title prefix is the one thing that genuinely
needs the number, and a BE-creation PR is not required to carry it: `scripts/lint_pr.py` fires its
title check only when the branch name encodes a real id, which a branch like `claude/<topic>` never
does, so a plain scoped title is correct here exactly as it is for every proposal PR.

Second, the allocator ([`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py))
rewrites `BE-XXXX` → `BE-NNNN` **only inside the item's own directory** — it does not sweep the
repository. So the single constraint merge-time allocation imposes on this flow is that the
`BE-XXXX` placeholder must appear nowhere but the item's own files. A placeholder written into
implementation code, a test, a comment, or a doc elsewhere would survive the merge as a stale
reference on `main`. Merge-time allocation therefore *shapes* the single PR — it fixes where the id
may appear — rather than ruling the single PR out.

### Why a skill, not a note in the docs

The single-PR flow has a small, precise discipline that is easy to get wrong: keep the placeholder id
confined to the item's own files, set `Status: Implemented` on the placeholder rather than waiting
for a number, keep the title plain, and merge by a path that actually fires `roadmap-id`. Encoding
the flow as a skill — the same way `ideation` and `implement-be` encode theirs — makes the safe path
the easy path and keeps the two existing skills unchanged and single-purpose.

## Detailed design

`propose-and-build` is a **composition** of the two existing skills, not a rewrite of either. It
reuses `ideation`'s authoring rules and `implement-be`'s implementation and promotion rules verbatim;
its only new content is the single-PR choreography, the id-confinement invariant, and the
prime-directive-driven guardrails on *when* to use it.

### The single PR and its lifecycle

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

The `BE-XXXX` placeholder is live for the whole of the PR's review, and the merge is the single point
at which the number is fixed. This keeps the design compatible with BE-0089 — nothing changes about
*when* the number is allocated; the skill only arranges the PR so that the merge-time rewrite lands a
finished item.

### Work breakdown (MECE)

1. **Author the SKILL.md.** Create `.claude/skills/propose-and-build/SKILL.md` with frontmatter
   (`name: propose-and-build`, a `description` whose trigger phrases cover "propose and build at
   once" and "write the BE and the code together"; a default `model:` per BE-0103) and a body
   structured as the phases below. It links to `ideation` and `implement-be` rather than duplicating
   their content.
2. **Phase A — author the proposal (delegates to `ideation`'s rules).** Branch `claude/<topic>` off
   `origin/main`; scaffold `BE-XXXX-<slug>/` with `make new-roadmap-item` at `Status: Proposal`; fill
   it under the [`document-writing`](../../.claude/skills/document-writing/SKILL.md) skill and
   localize the Japanese under [`japanese-document-writing`](../../.claude/skills/japanese-document-writing/SKILL.md).
   Identical to `ideation`, and the skill says so rather than restating it.
3. **Phase B — implement against the placeholder (delegates to `implement-be` steps 3–9).** On the
   **same branch**, treat the `BE-XXXX-<slug>/` proposal as the spec and run `implement-be`'s
   ground-in-the-code, plan-and-confirm, implement-with-tests, and review-the-diff steps. Then run
   its promotion steps **now**, because `Status` and the PR number do not depend on the id: flip
   `Status: Implemented` and tick the `Progress` boxes in both language files, keeping the `BE-XXXX`
   placeholder, and add the `Implementing PR` row referencing this PR's own number. Two steps stay
   deferred because they need a number or an issue that exists only after merge: the `[BE-NNNN]` title
   prefix (the PR keeps a plain scoped title), and the tracking-issue self-assign (the
   [BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) issue is created
   only after the numbered item exists on `main`). Because this is a BE-creation PR whose id is
   allocated only when a human merges it, the skill does not auto-create it: it pushes the branch and
   lets the human open it (BE-0230), as a Draft since it carries product code.
4. **The one invariant — confine the placeholder id.** The skill states plainly that `BE-XXXX` must
   appear **nowhere but the item's own directory** (`roadmaps/BE-XXXX-<slug>/`), because the allocator
   rewrites only that directory. Refer to the feature by name in code; keep the id in the roadmap item,
   the one place the allocator fixes. A reference elsewhere lands stale on `main` and CI does not catch
   it (the roadmap format check walks only `roadmaps/`).
5. **Merge must trigger allocation.** The skill notes that a merge performed with the default
   `GITHUB_TOKEN` (native auto-merge) does not re-trigger `push`-driven workflows, so `roadmap-id`
   would never run and the PR would land with `BE-XXXX` — code and all — unallocated on `main`. The PR
   must be merged by a path that fires `roadmap-id` (a manual merge, or auto-merge with the roadmap App
   token). This is the same constraint a plain `ideation` proposal already lives under; it only matters
   more here because the implementation lands with the item.
6. **Guardrails and scope (prime-directive-driven).** The skill states plainly *when* to reach for it
   and when not to:
   - Use it only for a small, well-scoped item whose design the author does not expect to change
     materially in review. One PR fuses the design checkpoint with code review, so merging accepts the
     proposal and the implementation at once — the skill calls this out as the accepted trade-off and
     says to fall back to the serial path when a design is genuinely uncertain.
   - It authors *and* implements, so — unlike `ideation`, which never touches product code — it
     **does** modify `bajutsu/` / `BajutsuKit/` / tests, under `implement-be`'s rules. The skill is
     explicit that Phase A stays authoring-only and product code appears only in Phase B.
   - All three prime directives bind exactly as they do for `implement-be`: no LLM on the `run`/CI
     verdict path, determinism first, app-agnostic config. The skill adds no automation to CI — it is a
     human/agent procedure only — so it introduces no new machinery on any gate.
7. **Docs.** Keep the three-skill triangle in [`docs/ai-development.md`](../../docs/ai-development.md)
   (and its `docs/ja/` mirror) and the contributor tutorial in step with the skill — `ideation`
   (author only), `implement-be` (ship a numbered item), `propose-and-build` (both, in one PR) — and
   cross-link the three SKILL.md files' `References`.

### Machine-checkable outcome

The skill is a procedure, so the deterministic outcome is the existing gate applied to whatever the
PR produces: `make check` green, the roadmap dashboard reflecting `Status: Implemented` once the id is
allocated, and — per CI's `pr-title` check — the plain scoped title a BE-creation branch is required
to keep (the check fires only when the branch encodes a real id, which this flow's branch never
does). No new checker is added; the skill's correctness reduces to the already-gated `implement-be`
end state plus the id-confinement invariant the SKILL.md encodes.

## Alternatives considered

- **Do nothing; keep the strict serial `ideation` → `implement-be` split.** The safe default and
  still the right choice for most items. Rejected as the *only* option because it imposes avoidable
  latency and context re-acquisition on small items the author is ready to build immediately — the
  motivation above.
- **A temporary two-PR stack (the original design of this item): a proposal PR, an implementation PR
  stacked on its branch, and a hand-off that rebases and retargets the implementation onto `main`
  after the proposal merges.** This was how `propose-and-build` first shipped (#848). The stack existed
  to buy back the real id before finalizing the implementation, so the implementation PR could take the
  `[BE-NNNN]` title prefix and be rebased against a settled spec. Within the skill's own scope — a small
  design the author is confident about — neither payoff holds: the prefix is unnecessary (a BE-creation
  PR keeps a plain title), and there is no spec to re-settle against. Meanwhile the stack costs a second
  branch, keeping the draft PR rebased as the proposal evolves, a base retarget, and an error-prone
  hand-off rebase. Superseded by the single-PR design (#1304), which drops all of that for one added
  rule (confine the placeholder id).
- **A single mega-PR with no `BE-XXXX` discipline.** Simplest branching, and the design this item now
  adopts — but only *with* the id-confinement invariant. Without it, an id written into the
  implementation would land stale on `main`, because the allocator rewrites only the item's own
  directory. The invariant is what makes the single PR safe rather than merely simple; the skill states
  it as a hard rule, not a convention.
- **Allocate the id early (at PR-open or approval) so the PR could carry a real number throughout.**
  Would let the PR take a `[BE-NNNN]` prefix from the start, but it re-opens precisely the decision
  BE-0089 closed: a number spent before the item is accepted, and non-contiguous ids when a PR is
  rejected. A per-skill exception to a repo-wide invariant is worse than the plain title it would buy.
  Rejected.
- **Enforce the id-confinement invariant in CI (a check that no `BE-XXXX` appears outside a placeholder
  item's own directory).** Attractive for turning the one rule into a gate, but a repo-wide `BE-XXXX`
  sweep has to distinguish legitimate placeholder self-references, prose examples, and title-prefix
  examples from genuine stale references — the same false-positive surface the roadmap format check
  already navigates within `roadmaps/`. Deferred: the skill states the invariant as a discipline first;
  a scoped check is a possible follow-up once the flow has proven out.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Author `.claude/skills/propose-and-build/SKILL.md` (frontmatter + body).
- [x] Phase A section — delegate to `ideation`'s authoring rules.
- [x] Phase B section — delegate to `implement-be` steps 3–9, promoting on the placeholder.
- [x] The one invariant — confine the `BE-XXXX` placeholder to the item's own files.
- [x] Merge-must-trigger-allocation note (`GITHUB_TOKEN` auto-merge suppresses `roadmap-id`).
- [x] Guardrails / scope section (when to use, the fused-checkpoint trade-off, prime-directive binding).
- [x] `docs/ai-development.md` (+ `docs/ja/`) three-skill triangle and contributor tutorial in step;
      cross-link the three SKILL.md `References`.

### Log

- [#848](https://github.com/bajutsu-e2e/bajutsu/pull/848) — Ship the `propose-and-build` skill: add `.claude/skills/propose-and-build/SKILL.md`
  (frontmatter + Phase A / Phase B / hand-off / guardrails), cross-link the `ideation` and
  `implement-be` skills both ways, and document the three-skill triangle in
  `docs/ai-development.md` (+ `docs/ja/` mirror) and the skill-tier list.
- [#1257](https://github.com/bajutsu-e2e/bajutsu/pull/1257) — Retire `make roadmap-index` from the
  promotion steps: the roadmap dashboard now covers what the generated README index tables did, so
  there is nothing left to regenerate there.
- [#1304](https://github.com/bajutsu-e2e/bajutsu/pull/1304) — Rework the skill from the temporary
  two-PR stack to a single BE-creation PR. The item keeps the `BE-XXXX` placeholder and reaches
  `Status: Implemented` in the PR (status and the PR number do not depend on the id); CI allocates the
  real `BE-NNNN` on merge and rewrites the placeholder inside the item's own files. The hand-off,
  stacked branch, and base retarget are gone, replaced by one invariant — the placeholder id appears
  nowhere but the item's own files — and a note that the merge must fire `roadmap-id`.

## References

- [`ideation`](../../.claude/skills/ideation/SKILL.md) — the upstream skill this composes for
  Phase A (authoring only, never implements).
- [`implement-be`](../../.claude/skills/implement-be/SKILL.md) — the skill this composes for Phase B
  (steps 3–9); the deterministic counterpart that ships a numbered item.
- [BE-0089 — Merge-time BE-ID allocation on main](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)
  — the constraint the single PR satisfies: the real id exists only after the PR merges, and the
  allocator's item-only rewrite scope is the reason for the id-confinement invariant.
- [BE-0061 — Collision-proof BE-ID allocation](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
  — why guessing an id in parallel is unsafe; the reason this flow keeps the placeholder rather than
  pre-empting the number.
- [BE-0109 — GitHub Issues as the ownership tracker for open roadmap items](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md)
  — the tracking issue `implement-be` self-assigns; it does not exist during Phase B, so the skill
  defers the self-assign.
- [BE-0103 — Right-size the model and reasoning effort per development task](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md)
  — the default `model:` the SKILL.md's frontmatter carries.
- [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) — the allocator whose
  item-only rewrite scope is the reason for the id-confinement invariant.
- [`scripts/lint_pr.py`](../../scripts/lint_pr.py) · [`.github/workflows/pr-title.yml`](../../.github/workflows/pr-title.yml)
  — the CI check that keeps a BE-creation branch on a plain scoped title; it fires only when a branch
  encodes a real id, which this flow's branch never does.
- [`CLAUDE.md`](../../CLAUDE.md) · [`docs/ai-development.md`](../../docs/ai-development.md) — the
  parallel-work rules and the three-skill triangle this documents.

**English** · [日本語](BE-0216-propose-and-build-parallel-skill-ja.md)

# BE-0216 — propose-and-build: author a BE proposal and its implementation in parallel, stacked

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0216](BE-0216-propose-and-build-parallel-skill.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0216") |
| Implementing PR | [#848](https://github.com/bajutsu-e2e/bajutsu/pull/848) |
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
time, then land them as a two-PR stack — **the BE proposal first, the implementation second**. It
does not replace `ideation` / `implement-be`, and it does not change BE-0089's merge-time allocation:
the stack is a *temporary* arrangement that exists only while the proposal PR is in review, and it
collapses into two independent PRs the moment the proposal merges and its id is allocated. The
serial path remains the default for anything whose design deserves review before code is written.

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

### But naively parallelising collides with merge-time allocation

The obvious fix — "just write the code alongside the proposal" — runs straight into BE-0089. Until
the proposal merges, the item has **no** real id: it is `BE-0216` in the tree, the proposal PR
carries **no** `[BE-NNNN]` title prefix by convention (`scripts/lint_pr.py` does not require or
reject one here — its title check only fires when the branch name encodes a real id, which a
BE-creation branch like `claude/<topic>` never does), and the implementation therefore cannot
honour the two `implement-be` steps that *depend* on a real
id — flipping the item to `Status: Implemented` with an `Implementing PR` row, and prefixing the
implementation PR title `[BE-NNNN]`. So a parallel flow needs an explicit, ordered **hand-off**: a
defined point at which the id becomes known and the implementation branch adopts it. Without that
discipline, "parallel" degrades into two half-synchronised branches that each guess at a number —
exactly the collision-prone, gap-prone state BE-0061 and BE-0089 were built to prevent.

### Why a skill, not a note in the docs

The hand-off has a precise, error-prone sequence (rebase after the proposal merges, rewrite the now-
stale `BE-0216` references in the implementation diff to the allocated `BE-NNNN`, retarget the
stacked PR's base from the proposal branch to `main`, *then* run `implement-be`'s promotion + gate
steps). Encoding it as a skill — the same way `ideation` and `implement-be` encode their flows —
makes the safe path the easy path and keeps the two existing skills unchanged and single-purpose.

## Detailed design

`propose-and-build` is a **composition** of the two existing skills around an explicit hand-off, not
a rewrite of either. It reuses `ideation`'s authoring rules and `implement-be`'s implementation and
promotion rules verbatim; its only new content is the stacking and hand-off choreography and the
prime-directive-driven guardrails on *when* to use it.

### The two-PR stack and its lifecycle

```
                 (author is confident in a small, scoped feature)
                                    │
      ┌─────────────────────────────┴─────────────────────────────┐
      ▼                                                             ▼
  PR #1  BE proposal                                     PR #2  implementation
  branch: claude/<topic>                                 branch: claude/<topic>-impl
  base:   main                                           base:   claude/<topic>   (stacked)
  BE-0216-<slug>/ @ Status: Proposal                     code + tests against the BE-0216 spec
  plain scoped title, NO [BE-…] prefix                   DRAFT, NO Status flip, NO [BE-…] prefix
      │                                                             │
      │  reviewed & merged as-is (BE-0216 intact)                   │  kept in draft, rebased as #1 evolves
      ▼                                                             │
  roadmap-id allocates BE-NNNN on main (BE-0089)                    │
      │                                                             │
      └──────────────────────── HAND-OFF ──────────────────────────┘
                                    │
                                    ▼
   #2: rebase onto origin/main · rewrite BE-0216→BE-NNNN in the diff · retarget base→main
       · run implement-be steps 8–10 (flip Status, Implementing PR row, reindex, gate, [BE-NNNN] title)
```

The stack is **temporary**: PR #2's base is the proposal branch only while #1 is open. Once #1 merges
and the id is allocated, #2 is rebased onto `main` and retargeted, becoming an ordinary
`implement-be`-shaped PR. This is what keeps the design compatible with BE-0089 — nothing changes
about *when* the number is allocated; the skill only defines how the implementation branch adopts it
afterward.

### Work breakdown (MECE)

1. **Author the SKILL.md.** Create `.claude/skills/propose-and-build/SKILL.md` with frontmatter
   (`name: propose-and-build`, a `description` whose trigger phrases cover "propose and build at
   once", "write the BE and the code together", "stack the implementation on the proposal"; a
   default `model:` per BE-0103) and a body structured as the phases below. It links to `ideation`
   and `implement-be` rather than duplicating their content.
2. **Phase A — author the proposal (delegates to `ideation`'s rules).** Branch `claude/<topic>` off
   `origin/main`; scaffold `BE-0216-<slug>/` with `make new-roadmap-item` at `Status: Proposal`;
   fill it and localize the Japanese under the [`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/SKILL.md)
   skill. Open PR #1 with a plain scoped title and **no** `[BE-…]` prefix. Identical to `ideation`,
   and the skill says so rather than restating it.
3. **Phase B — implement against the placeholder (delegates to `implement-be` steps 3–7).** Create
   `claude/<topic>-impl` **stacked on** `claude/<topic>` (or an isolated `make worktree` workspace, so the two
   can truly proceed in parallel). Treat the `BE-0216-<slug>/` proposal as the spec, and run
   `implement-be`'s ground-in-the-code, plan-and-confirm, implement-with-tests, and review-the-diff
   steps — **with two carve-outs that depend on the not-yet-allocated id**: do **not** flip
   `Status` to Implemented, and do **not** add the `[BE-NNNN]` prefix. Open PR #2 as a **draft**
   with `base: claude/<topic>`.
4. **The tracking issue does not exist yet.** `implement-be` step 2 self-assigns the item's
   `roadmap-tracking` issue, but that issue is created by the [BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md)
   sync only after the numbered item exists on `main`. During Phase B there is nothing to claim; the
   skill notes this and defers the self-assign to the hand-off (or the first `implement-be`-style
   run after it).
5. **Hand-off (the load-bearing new step).** Triggered by PR #1 merging and `roadmap-id` allocating
   `BE-NNNN` on `main`. On the implementation branch, in order:
   - `git fetch origin && git rebase origin/main` — pulls in the merged proposal *and* the renumber
     commit. The `BE-0216-<slug>/` → `BE-NNNN-<slug>/` directory rename is a git rename; conflicts
     are mechanical.
   - Rewrite every `BE-0216` reference that lives **inside PR #2's own diff** (doc cross-refs, test
     fixtures, comments) to the allocated `BE-NNNN`. The proposal's own files were already
     renumbered on `main` by the workflow; this step only fixes references the *implementation*
     introduced.
   - Retarget the PR base from the proposal branch to `main` (`gh pr edit --base main`). GitHub
     auto-retargets a stacked PR when its base branch is deleted on merge, but the skill makes it
     explicit so the flow does not depend on the base branch being deleted.
   - Now that `BE-NNNN` exists: self-assign the tracking issue (`implement-be` step 2), and run
     `implement-be` steps 8–10 — flip `Status: Implemented` + add the `Implementing PR` row in both
     language files, `make roadmap-index`, `make check`, mark the PR ready, add the `[BE-NNNN]`
     title prefix.
6. **Guardrails and scope (prime-directive-driven).** The skill states plainly *when* to reach for
   it and when not to:
   - Use it only for a small, well-scoped item whose design the author does not expect to change
     materially in review. If review reshapes the proposal, the implementation branch eats the
     rework — the skill calls this out as the accepted trade-off, and says to fall back to the
     serial path when a design is genuinely uncertain.
   - It authors *and* implements, so — unlike `ideation`, which never touches product code — it
     **does** modify `bajutsu/` / `BajutsuKit/` / tests, under `implement-be`'s rules. The skill is
     explicit that Phase A stays authoring-only and product code appears only in Phase B, on the
     separate implementation branch.
   - All three prime directives bind exactly as they do for `implement-be`: no LLM on the
     `run`/CI verdict path, determinism first, app-agnostic config. The skill adds no automation to
     CI — it is a human/agent procedure only — so it introduces no new machinery on any gate.
7. **Docs.** Add a short subsection to [`docs/ai-development.md`](../../docs/ai-development.md) (and
   its `docs/ja/` mirror) describing the three-skill triangle — `ideation` (author only),
   `implement-be` (ship a numbered item), `propose-and-build` (both, stacked) — and when to pick
   each. Cross-link the three SKILL.md files' `References`.

### Machine-checkable outcome

The skill is a procedure, so the deterministic outcome is the existing gate applied to whatever the
implementation branch produces: `make check` green on PR #2 after the hand-off, the roadmap index
reflecting `Status: Implemented`, and — enforced by CI's `pr-title` check once the (post-hand-off)
implementation branch encodes `BE-NNNN` — the matching `[BE-NNNN]` title prefix on that PR. The
proposal PR's title is not itself checked for the *absence* of a prefix (`scripts/lint_pr.py` only
validates the prefix when the branch encodes a real id, which a BE-creation branch never does); its
plain title is a convention the skill follows, not something CI enforces. No new checker is added;
the skill's correctness reduces to the already-gated `implement-be` end state plus the ordering
discipline the SKILL.md encodes.

## Alternatives considered

- **Do nothing; keep the strict serial `ideation` → `implement-be` split.** The safe default and
  still the right choice for most items. Rejected as the *only* option because it imposes avoidable
  latency and context re-acquisition on small items the author is ready to build immediately — the
  motivation above.
- **Persistent stacked PR: keep the implementation PR based on the proposal branch through to
  merge, and merge the stack in order.** Cleaner-looking as a "true" stack, but it fights BE-0089
  head-on: the implementation PR would need a real id (for its `Status` flip and title prefix)
  *before* the proposal merges, which is exactly what merge-time allocation refuses to give. Landing
  it would mean re-introducing pre-merge allocation and its gap/collision problems (BE-0061,
  BE-0089). Rejected — the temporary-stack-then-flatten design gets the parallelism without touching
  allocation timing.
- **Allocate the id early (at PR-open or approval) just for this flow.** Would make a persistent
  stack work, but it re-opens precisely the decision BE-0089 closed (a number spent before the item
  is accepted; non-contiguous ids). A per-skill exception to a repo-wide invariant is worse than the
  serialization it removes. Rejected.
- **A single mega-PR carrying the proposal and the implementation together.** Simplest branching,
  but it collapses the "proposal reviewed on its own merits" separation the two-stage roadmap is
  built around, and it defeats BE-0089 (the item would be numbered and implemented in one shot with
  no `BE-0216` window). It also makes the proposal PR carry product code, muddying review. Rejected;
  the two-PR stack keeps the proposal reviewable in isolation.
- **Automate the hand-off in CI (a workflow that rebases/renumbers the implementation branch when
  the proposal merges).** Attractive for removing the manual rebase, but it adds bypass-capable
  automation touching a contributor's implementation branch and product code, well beyond the narrow
  `roadmaps/**` blast radius BE-0089 so carefully bounds. Deferred: the skill defines the manual
  procedure first; a helper (e.g. a `make` target that runs the rebase + reference rewrite) is a
  possible follow-up once the flow has proven out, but full CI automation is out of scope.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Author `.claude/skills/propose-and-build/SKILL.md` (frontmatter + body).
- [x] Phase A section — delegate to `ideation`'s authoring rules.
- [x] Phase B section — delegate to `implement-be` steps 3–7 with the two id-dependent carve-outs.
- [x] Tracking-issue deferral note (BE-0109 issue does not exist during Phase B).
- [x] Hand-off section — rebase, `BE-0216`→`BE-NNNN` diff rewrite, base retarget, `implement-be`
      steps 8–10.
- [x] Guardrails / scope section (when to use, the rework trade-off, prime-directive binding).
- [x] `docs/ai-development.md` (+ `docs/ja/`) three-skill triangle subsection; cross-link the three
      SKILL.md `References`.

### Log

- [#848](https://github.com/bajutsu-e2e/bajutsu/pull/848) — Ship the `propose-and-build` skill: add `.claude/skills/propose-and-build/SKILL.md`
  (frontmatter + Phase A / Phase B / hand-off / guardrails), cross-link the `ideation` and
  `implement-be` skills both ways, and document the three-skill triangle in
  `docs/ai-development.md` (+ `docs/ja/` mirror) and the skill-tier list.
- [#1257](https://github.com/bajutsu-e2e/bajutsu/pull/1257) — Retire `make roadmap-index` from the
  hand-off's step 8–10 list above: the roadmap dashboard now covers what the generated README index
  tables did, so there is nothing left to regenerate there. The rest of the hand-off (rebase, id
  rewrite, base retarget, `Status` flip) is unaffected.

## References

- [`ideation`](../../.claude/skills/ideation/SKILL.md) — the upstream skill this composes for
  Phase A (authoring only, never implements).
- [`implement-be`](../../.claude/skills/implement-be/SKILL.md) — the skill this composes for Phase B
  and the hand-off (steps 3–10); the deterministic counterpart that ships a numbered item.
- [BE-0089 — Merge-time BE-ID allocation on main](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)
  — the constraint that makes the stack necessarily *temporary*: the real id exists only after the
  proposal merges, so the hand-off is the point at which the implementation branch adopts it.
- [BE-0061 — Collision-proof BE-ID allocation](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
  — why guessing an id in parallel is unsafe; the reason the stack defers to allocation rather than
  pre-empting it.
- [BE-0109 — GitHub Issues as the ownership tracker for open roadmap items](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md)
  — the tracking issue `implement-be` self-assigns; it does not exist during Phase B, so the skill
  defers the self-assign to the hand-off.
- [BE-0103 — Right-size the model and reasoning effort per development task](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md)
  — the default `model:` the new SKILL.md's frontmatter carries.
- [`scripts/lint_pr.py`](../../scripts/lint_pr.py) · [`.github/workflows/pr-title.yml`](../../.github/workflows/pr-title.yml)
  — the CI check that enforces the matching `[BE-NNNN]` prefix once a branch encodes a real id
  (post-hand-off implementation PR); it does not check the proposal PR's title for the prefix's
  absence, since a BE-creation branch never encodes an id in the first place.
- [`CLAUDE.md`](../../CLAUDE.md) · [`docs/ai-development.md`](../../docs/ai-development.md) — the
  parallel-work rules and the three-skill triangle this documents.

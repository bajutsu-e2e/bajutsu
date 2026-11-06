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
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's roadmap workflow is deliberately two-staged. The [`ideation`](../../.claude/skills/ideation/SKILL.md)
skill *authors* a BE (Bajutsu Evolution) proposal and stops at the roadmap files. The
[`implement-be`](../../.claude/skills/implement-be/SKILL.md) skill *ships* an already-numbered item
from its id. The two are strictly serial by construction. A permanent `BE-NNNN` id is allocated only
**after** the proposal PR merges to `main` ([BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)),
so implementation cannot start under a real id until the proposal has landed.

This item adds a **third** skill, `propose-and-build`. It serves the case where the author is
confident enough in a small, well-scoped feature to write the proposal *and* its implementation at
the same time. The author then lands both in a **single BE-creation PR** that carries the roadmap
item, the code, and the tests together.

The skill does not replace `ideation` / `implement-be`, and it does not change the merge-time
allocation of BE-0089. The item keeps the `BE-XXXX` placeholder throughout the life of the PR, and CI
allocates the real id the moment the PR merges, exactly as for any proposal. The serial path remains
the default for anything whose design deserves review before anyone writes the code.

## Motivation

### The serial path has real latency, and for small items that latency is pure overhead

The `ideation` → merge → allocate → `implement-be` chain is the right default. It forces a design to
clear review before anyone writes code against it. It also keeps the `BE-NNNN` sequence contiguous.
A number is spent only on an item that actually ships (BE-0089). But the chain serializes two
activities. For a small and low-risk feature, the same author often already holds both fully formed
in their head. For such an item the author must:

1. run `ideation`, open the proposal PR, and **wait** for it to merge;
2. wait for the `roadmap-id` workflow to allocate `BE-NNNN` on `main`;
3. only then run `implement-be BE-NNNN`, re-establishing all the context they had at step 1.

The wait between (1) and (3) is dead time for a change that the author is ready to build now. Step 3
also pays a context-reacquisition cost.
The author re-reads the proposal, the touch-points, and the surrounding code. The author already paid that cost during ideation. No prime directive requires this
serialization. It falls out of the id-allocation timing, not out of any correctness need.

### Merge-time allocation shapes the single PR; it does not block it

The obvious fix is to write the code alongside the proposal and land both together. That looks as
though it collides with BE-0089, because until the PR merges the item has no real id. It does not
collide, once two facts are separated.

First, the two finishing touches of an `implement-be` PR do **not** both need the number.

- Flipping the item to `Status: Implemented` needs no id.
  Recording an `Implementing PR` row needs only the PR number. The item can therefore reach `Status: Implemented`
  **while it still holds the `BE-XXXX` placeholder**.
- The `roadmap-id` workflow rewrites the placeholder to the allocated `BE-NNNN` on merge. The item
  lands numbered and Implemented, with no post-merge fixup.
- The `[BE-NNNN]` title prefix is the one thing that genuinely needs the number. A BE-creation PR is
  not required to carry it. `scripts/lint_pr.py` fires its title check only when the branch name
  encodes a real id.
  A branch like `claude/<topic>` never does. A plain scoped title is correct
  here, exactly as it is for every proposal PR.

Second, the allocator ([`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py))
rewrites `BE-XXXX` → `BE-NNNN` **only inside the item's own directory**. It does not sweep the
repository. Merge-time allocation therefore imposes one constraint on this flow.
The `BE-XXXX` placeholder must appear nowhere but the item's own files.
A placeholder in code, a test, a comment, or a doc elsewhere would survive the merge.
It would sit on `main` as a stale reference.

Merge-time allocation thus *shapes* the single PR by fixing where the id may appear. It does not rule
the single PR out.

### Why a skill, not a note in the docs

The single-PR flow has a small, precise discipline that is easy to get wrong:

- keep the placeholder id confined to the item's own files;
- set `Status: Implemented` on the placeholder rather than waiting for a number;
- keep the title plain;
- merge by a path that actually fires `roadmap-id`.

Encoding the flow as a skill makes the safe path the easy path.
`ideation` and `implement-be` encode their flows the same way.
Both stay unchanged and single-purpose.

## Detailed design

`propose-and-build` is a **composition** of the two existing skills, not a rewrite of either. It
reuses the authoring rules of `ideation` and the implementation and promotion rules of `implement-be`
verbatim. Its new content is three things:
the single-PR choreography, the id-confinement invariant, and guardrails on *when* to use it.
The prime directives drive those guardrails.

### The single PR and its lifecycle

```
                 (author is confident in a small, scoped feature)
                                    │
                                    ▼
   one branch: claude/<topic>   base: main
     roadmaps/BE-XXXX-<slug>/  @ Status: Implemented   (placeholder id; BE-XXXX only
     code + tests against the proposal spec             inside the item's own files)
     plain scoped title, NO [BE-…] prefix
                                    │
                                    │  human opens the PR; the Implementing PR row's number
                                    │  is filled in a follow-up push, then it is merged
                                    ▼
   roadmap-id allocates BE-NNNN on main (BE-0089)
     · renames BE-XXXX-<slug>/ → BE-NNNN-<slug>/
     · rewrites BE-XXXX → BE-NNNN inside the item's own files
                                    │
                                    ▼
   item lands numbered and Implemented — no post-merge fixup
```

The `BE-XXXX` placeholder is live for the whole review of the PR. The merge is the single point at
which the number is fixed. This keeps the design compatible with BE-0089.
Nothing changes about *when* the number is allocated. The skill only arranges the PR so that the merge-time rewrite
lands a finished item.

### Work breakdown (MECE)

1. **Author the SKILL.md.** Create `.claude/skills/propose-and-build/SKILL.md` with frontmatter and a
   body structured as the phases below.
   - The frontmatter has `name: propose-and-build`, a default `model:` per BE-0103, and a
     `description` whose trigger phrases cover "propose and build at once" and "write the BE and the
     code together".
   - The body links to `ideation` and `implement-be` rather than duplicating their content.
2. **Phase A — author the proposal (delegates to the rules of `ideation`).** Branch `claude/<topic>`
   off `origin/main`. Scaffold `BE-XXXX-<slug>/` with `make new-roadmap-item` at `Status: Approved`.
   Fill it under the [`document-writing`](../../.claude/skills/document-writing/SKILL.md) skill, and
   localize the Japanese under [`japanese-document-writing`](../../.claude/skills/japanese-document-writing/SKILL.md).
   This is identical to `ideation`, and the skill says so rather than restating it.
3. **Phase B — implement against the placeholder (delegates to `implement-be` steps 3–9).** On the
   **same branch**, treat the `BE-XXXX-<slug>/` proposal as the spec. Run the steps of `implement-be`
   that ground the work in the code, plan and confirm, implement with tests, and review the diff.
   Then run its promotion steps **now**, because `Status` and the PR number do not depend on the id:
   - Flip `Status: Implemented` and tick the `Progress` boxes in both language files, keeping the
     `BE-XXXX` placeholder.
   - Add the `Implementing PR` row. The PR number does not exist during Phase B.
     Its number is filled in a follow-up push, once the human has opened the PR. This matches how
     `implement-be` step 8 defers the fill to step 10.
   - Two steps stay deferred, because they need a number or an issue that exists only after merge.
     One is the `[BE-NNNN]` title prefix, so the PR keeps a plain scoped title. The other is the
     tracking-issue self-assign, because the
     [BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) issue is
     created only after the numbered item exists on `main`.
   - This is a BE-creation PR, and its id is allocated only when a human merges it. The skill
     therefore does not auto-create the PR. It pushes the branch and lets the human open it
     (BE-0230), as a Draft since the PR carries product code.
4. **The one invariant — confine the placeholder id.** The skill states plainly that `BE-XXXX` must
   appear **nowhere but the item's own directory** (`roadmaps/BE-XXXX-<slug>/`), because the allocator
   rewrites only that directory. Code refers to the feature by name. The id stays in the roadmap item,
   the one place the allocator fixes. A reference elsewhere lands stale on `main`, and CI does not
   catch it, because the roadmap format check walks only `roadmaps/`.
5. **Merge must trigger allocation.** A merge performed with the default `GITHUB_TOKEN` (native
   auto-merge) does not re-trigger `push`-driven workflows. In that case `roadmap-id` never runs, and
   the PR lands on `main` with `BE-XXXX` unallocated, code and all. The skill notes this. The PR must
   be merged by a path that fires `roadmap-id`: a manual merge, or auto-merge with the roadmap App
   token. A plain `ideation` proposal already lives under the same constraint. It matters more here
   because the implementation lands with the item.
6. **Guardrails and scope (prime-directive-driven).** The skill states plainly *when* to reach for it
   and when not to:
   - Use it only for a small, well-scoped item.
     The author must not expect the design to change materially in review.
     One PR fuses the design checkpoint with code review.
     Merging therefore accepts the proposal and the implementation at once. The skill calls this the accepted trade-off. It says to
     fall back to the serial path when a design is genuinely uncertain.
   - The skill authors *and* implements. Unlike `ideation`, which never touches product code, it
     **does** modify `bajutsu/` / `BajutsuKit/` / tests, under the rules of `implement-be`. The skill
     is explicit that Phase A stays authoring-only and product code appears only in Phase B.
   - All three prime directives bind as they do for `implement-be`.
     No LLM sits on the `run`/CI verdict path.
     Determinism comes first, and config stays app-agnostic. The skill is a human/agent procedure
     only and adds no automation to CI, so it introduces no new machinery on any gate.
7. **Docs.** Keep the three-skill triangle in [`docs/ai-development.md`](../../docs/ai-development.md)
   (and its `docs/ja/` mirror) and the contributor tutorial in step with the skill. `ideation` is
   author only, `implement-be` ships a numbered item, and `propose-and-build` does both in one PR.
   Cross-link the `References` of the three SKILL.md files.

### Machine-checkable outcome

The skill is a procedure.
The deterministic outcome is the existing gate, applied to whatever the PR produces. `make check` is green. The roadmap dashboard reflects `Status: Implemented` once the id
is allocated. Per the `pr-title` check of CI, the PR keeps the plain scoped title that a BE-creation
branch is required to keep. The check fires only when the branch encodes a real id, which the branch
of this flow never does.

No new checker is added. The correctness of the skill reduces to two things.
One is the already-gated end state of `implement-be`.
The other is the id-confinement invariant that the SKILL.md encodes.

## Alternatives considered

- **Do nothing; keep the strict serial `ideation` → `implement-be` split.** This is the safe default.
  It is still the right choice for most items. Rejected as the *only* option, because it imposes
  avoidable latency and context re-acquisition on small items that the author is ready to build
  immediately (see Motivation).
- **A temporary two-PR stack (the original design of this item).** The stack was a proposal PR, an
  implementation PR stacked on its branch, and a hand-off that rebases and retargets the
  implementation onto `main` after the proposal merges. This is how `propose-and-build` first shipped
  (#848). The stack existed to buy back the real id before finalizing the implementation. The
  implementation PR could then take the `[BE-NNNN]` title prefix and be rebased against a settled
  spec. Within the scope of the skill, a small design that the author is confident about, neither
  payoff holds. The prefix is unnecessary, because a BE-creation PR keeps a plain title. There is no
  spec to re-settle against. Meanwhile the stack costs a second branch, a draft PR kept rebased as the
  proposal evolves, a base retarget, and an error-prone hand-off rebase. The single-PR design (#1304)
  supersedes it and drops all of that for one added rule: confine the placeholder id.
- **A single mega-PR with no `BE-XXXX` discipline.** This has the simplest branching, and it is the
  design that this item now adopts, but only *with* the id-confinement invariant. Without the
  invariant, an id written into the implementation would land stale on `main`, because the allocator
  rewrites only the item's own directory. The invariant makes the single PR safe rather than merely
  simple, so the skill states it as a hard rule, not a convention.
- **Allocate the id early (at PR-open or approval) so the PR could carry a real number throughout.**
  The PR could then take a `[BE-NNNN]` prefix from the start. But this re-opens the decision that
  BE-0089 closed: a number spent before the item is accepted, and non-contiguous ids when a PR is
  rejected. A per-skill exception to a repo-wide invariant is worse than the plain title it would
  buy. Rejected.
- **Enforce the id-confinement invariant in CI.** The check would verify that no `BE-XXXX` appears
  outside the own directory of a placeholder item. It is attractive because it turns the one rule
  into a gate. But a repo-wide `BE-XXXX` sweep has to distinguish genuine stale references from
  legitimate placeholder self-references, prose examples, and title-prefix examples. The roadmap
  format check already navigates the same false-positive surface within `roadmaps/`. Deferred. The
  skill states the invariant as a discipline first, and a scoped check is a possible follow-up once
  the flow has proven out.

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

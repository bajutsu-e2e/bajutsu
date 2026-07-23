**English** · [日本語](BE-0096-docs-roadmap-link-integrity-ja.md)

# BE-0096 — Keep docs links to roadmap items from rotting on promotion

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0096](BE-0096-docs-roadmap-link-integrity.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0096") |
| Implementing PR | [#332](https://github.com/bajutsu-e2e/bajutsu/pull/332) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

When a roadmap item ships, `make roadmap-promote` moves its directory between status folders
(`proposals/` → `in-progress/` → `implemented/`, [BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md))
and repairs the cross-links to it **within `roadmaps/`**. It does not touch the links to that item
from `docs/` (or `README*` / `CLAUDE.md`), and the gate does not validate those links — so every
promotion silently rots the `docs/` links that point at the moved item. This item closes that gap:
make a broken `docs/` → roadmap link a gate failure, and extend the promote step's link repair to
cover `docs/` so the common case is fixed automatically.

## Motivation

The repository already treats roadmap cross-links as something to keep honest automatically:
`roadmap-promote` rewrites the folder segment of every link **inside `roadmaps/`** when an item
moves, and `make test` fails if the committed index drifts ([BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md),
[BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md)). But
that repair stops at the `roadmaps/` boundary, and the gate's `test_roadmap_format` only checks links
*between roadmap items*, never the links *from* `docs/` (or the top-level `README*` / `CLAUDE.md`) to
a roadmap item.

The consequence is silent rot. A doc that links `docs/drivers.md → roadmaps/in-progress/BE-0041-…`
keeps that path verbatim after BE-0041 is promoted to `implemented/`, so the link 404s, and nothing
in `make check` notices. This is not hypothetical: a single sweep found dead `docs/` links to
**BE-0041, BE-0010, BE-0050, BE-0063, BE-0065, BE-0068, and BE-0051** — all items that had shipped
and moved folder — which had to be repaired by hand. As more items ship, the rot only grows. The
fix should be the same kind the project already trusts for the `roadmaps/` side: a deterministic,
AI-free guard that runs on the Linux gate.

## Detailed design

Two complementary pieces — a **detector** in the gate and an **auto-repair** in the promote step —
both pure, deterministic, and Linux-only (no Simulator, no LLM, no network).

### 1. A gate check that every docs → roadmap link resolves

Add a test (run by `make test`, so it is part of `make check`) that scans every Markdown file under
`docs/` and the top-level `README.md` / `README.ja.md` / `CLAUDE.md`, extracts each relative link
whose target is under `roadmaps/`, resolves it relative to the linking file, and asserts the target
file exists. A missing target fails the gate with the offending `file:line → target`. This catches
**both** failure modes — a promotion that moved a target, and a hand-authored typo or stale path —
and it catches them at PR time rather than in a later manual sweep. The check is a pure function of
the file tree, so it needs no device and no model; it mirrors how `test_roadmap_format` already
guards links inside `roadmaps/`, extended one directory outward.

Scope of the check: only `roadmaps/`-targeted relative links (the surface that rots on promotion).
It is not a general external/HTTP link checker (that is heavier and network-bound — see Alternatives).

### 2. Extend `roadmap-promote`'s link repair to `docs/`

When `roadmap-promote` moves an item's directory, have it rewrite the moved folder segment
(`<old-status>/BE-NNNN-<slug>` → `<new-status>/BE-NNNN-<slug>`) in links across `docs/` and the
top-level `README*` / `CLAUDE.md`, exactly as it already does within `roadmaps/`. This turns the
common case — an item ships, its `docs/` links should follow — into an automatic, reviewable diff in
the same change, so the gate check (piece 1) rarely has to fail for a promotion. The rewrite is a
literal folder-segment substitution keyed by the item's slug, identical to the existing in-`roadmaps`
repair, so it carries the same low risk.

### Why both

The detector is the contract (it makes the invariant enforceable and also catches hand-authored
mistakes the auto-repair can't know about); the auto-repair is the convenience that keeps the
detector from firing on the routine promotion path. Either alone is weaker: a detector without repair
leaves every promotion a manual fix; a repair without a detector still lets typos and
non-promotion drift through. Shipping the detector first is the higher-value half and is independently
useful.

### Determinism & scope

This is contributor-workflow tooling: docs-only, no runtime or behavior change, no LLM anywhere, and
it runs entirely inside the existing Linux `make check` gate. It touches neither the drivers, the
runner, nor the determinism guarantees.

## Alternatives considered

- **Only the gate check, no auto-repair.** Simpler, and it makes the invariant enforceable, but it
  leaves every promotion a manual link fix — exactly the toil this item exists to remove. Worth
  shipping *first* (it is the contract), but not the whole answer.
- **Only the auto-repair, no gate check.** Fixes the routine promotion case but silently lets
  hand-authored typos and any path the repair didn't anticipate slip through. Without the detector
  there is no guarantee, only a best effort.
- **A general external link checker (HTTP + intra-repo).** Catches more (dead external URLs, anchor
  fragments), but it is heavier, network-dependent, and flaky against rate limits — a poor fit for a
  fast, hermetic gate. The rot this item targets is internal and structural; a narrow internal
  resolver is the deterministic, offline fit.
- **Leave it manual (status quo).** Rejected: the sweep that motivated this item already had to
  repair seven items' links by hand, and the cost grows with every promotion.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`scripts/promote_roadmap_items.py` (the promote step that repairs cross-links within `roadmaps/`),
`tests/test_roadmap_format.py` (the existing roadmap-link guard to extend outward), the `roadmap-promote`
Make target; [BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
(executable guardrails / self-healing git + roadmap tooling) and [BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md)
(status folders + promotion) — the precedents this builds on.

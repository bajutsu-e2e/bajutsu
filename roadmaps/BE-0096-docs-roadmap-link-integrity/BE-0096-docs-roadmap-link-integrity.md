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
and repairs the cross-links to it **within `roadmaps/`**. The step does not touch the links to that
item from `docs/` (or `README*` / `CLAUDE.md`), and the gate does not validate those links. Every
promotion thus rots the `docs/` links that point at the moved item, with no warning. This item closes
that gap in two ways. A broken `docs/` → roadmap link becomes a gate failure, and the promote step's
link repair extends to `docs/`, so the common case is fixed automatically.

## Motivation

The repository already keeps roadmap cross-links honest automatically. `roadmap-promote` rewrites
the folder segment of every link **inside `roadmaps/`** when an item moves, and `make test` fails if
the committed index drifts ([BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md),
[BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md)). That repair
stops at the `roadmaps/` boundary, and the gate's `test_roadmap_format` checks merely links
*between roadmap items*, never the links *from* `docs/` (or the top-level `README*` / `CLAUDE.md`) to
a roadmap item.

The consequence is silent rot. A doc that links `docs/drivers.md → roadmaps/in-progress/BE-0041-…`
keeps that path verbatim after BE-0041 is promoted to `implemented/`. The link 404s. Nothing
in `make check` notices. The problem is not hypothetical: a single sweep found dead `docs/` links to
**BE-0041, BE-0010, BE-0050, BE-0063, BE-0065, BE-0068, and BE-0051**. All seven items had shipped
and moved folder, and the links had to be repaired by hand. As more items ship, the rot only grows.
The fix should be the kind the project already trusts for the `roadmaps/` side: a deterministic,
AI-free guard that runs on the Linux gate.

## Detailed design

Two complementary pieces make up the design: a **detector** in the gate and an **auto-repair** in
the promote step. Both are pure, deterministic, and Linux-only. Neither needs a Simulator, an LLM,
or a network.

### 1. A gate check that every docs → roadmap link resolves

Add a test (run by `make test`, so it is part of `make check`) that scans every Markdown file under
`docs/` and the top-level `README.md` / `README.ja.md` / `CLAUDE.md`, extracts each relative link
whose target is under `roadmaps/`, resolves it from the linking file's directory, and asserts that
the target file exists. A missing target fails the gate with the offending `file:line → target`. The check
catches **both** failure modes: a promotion that moved a target, and a hand-authored typo or stale
path. It catches them at PR time rather than in a later manual sweep. The check is a pure function
of the file tree, so it needs no device and no model. It mirrors how `test_roadmap_format` already
guards links inside `roadmaps/`, extended one directory outward.

The check covers `roadmaps/`-targeted relative links alone, the surface that rots on promotion. The
check is not a general external/HTTP link checker, which is heavier and network-bound (see
Alternatives).

### 2. Extend `roadmap-promote`'s link repair to `docs/`

When `roadmap-promote` moves an item's directory, have it rewrite the moved folder segment
(`<old-status>/BE-NNNN-<slug>` → `<new-status>/BE-NNNN-<slug>`) in links across `docs/` and the
top-level `README*` / `CLAUDE.md`, exactly as it already does within `roadmaps/`. The common case is
an item that ships while its `docs/` links should follow. That case becomes an automatic, reviewable
diff in the same change, so the gate check (piece 1) rarely has to fail for a promotion. The rewrite
is a literal folder-segment substitution keyed by the item's slug, identical to the existing
in-`roadmaps` repair, so it carries the same low risk.

### Why both

The detector is the contract: it makes the invariant enforceable and also catches hand-authored
mistakes the auto-repair cannot know about. The auto-repair is the convenience that keeps the
detector from firing on the routine promotion path. Either alone is weaker. A detector without repair
leaves every promotion a manual fix, and a repair without a detector still lets typos and
non-promotion drift through. Shipping the detector first is the higher-value half and is independently
useful.

### Determinism & scope

This item is contributor-workflow tooling. It is docs-only, changes no runtime behavior, uses no LLM
anywhere, and runs entirely inside the existing Linux `make check` gate. It touches neither the
drivers, the runner, nor the determinism guarantees.

## Alternatives considered

- **Only the gate check, no auto-repair.** Simpler, and it makes the invariant enforceable, but it
  leaves every promotion a manual link fix, which is exactly the toil this item exists to remove. It
  is worth shipping *first* (it is the contract), but it is not the whole answer.
- **Only the auto-repair, no gate check.** Fixes the routine promotion case but silently lets
  hand-authored typos and any path the repair did not anticipate slip through. Without the detector
  there is no guarantee, only a best effort.
- **A general external link checker (HTTP + intra-repo).** Catches more (dead external URLs, anchor
  fragments), but it is heavier, network-dependent, and flaky against rate limits, which makes it a
  poor fit for a fast, hermetic gate. The rot this item targets is internal and structural, so a
  narrow internal resolver is the deterministic, offline fit.
- **Leave it manual (status quo).** Rejected, because the sweep that motivated this item already
  had to repair seven items' links by hand, and the cost grows with every promotion.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`scripts/promote_roadmap_items.py` (the promote step that repairs cross-links within `roadmaps/`),
`tests/test_roadmap_format.py` (the existing roadmap-link guard to extend outward), and the
`roadmap-promote` Make target. This item builds on two precedents:
[BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
(executable guardrails / self-healing git + roadmap tooling) and
[BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md)
(status folders + promotion).

**English** · [日本語](BE-0159-flatten-roadmap-status-folders-ja.md)

# BE-0159 — Flatten roadmap items into one directory (retire status-driven folders)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0159](BE-0159-flatten-roadmap-status-folders.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0159") |
| Implementing PR | [#628](https://github.com/bajutsu-e2e/bajutsu/pull/628) (1/2), [#631](https://github.com/bajutsu-e2e/bajutsu/pull/631) (2/2) |
| Topic | Contributor workflow |
| Related | [BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md), [BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md), [BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md), [BE-0149](../BE-0149-roadmap-placeholder-format-guardrail/BE-0149-roadmap-placeholder-format-guardrail.md), [BE-0154](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md) gave
each of the four `Status` values its own directory
(`roadmaps/{implemented,in-progress,proposals,deferred}/`), so a promotion `git mv`s an item's
directory every time its `Status` changes. This item proposes retiring that half of BE-0078: flatten
every item into a single `roadmaps/BE-NNNN-<slug>/`, so an item's path is fixed forever from the
moment its ID is allocated ([BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md))
and a promotion never touches the filesystem again. `Status` keeps deciding the generated index and
dashboard bucket — the reader-facing distinction BE-0078 actually wanted stays exactly as it is —
it just stops also deciding the file's location, which is the part that rots links.

## Motivation

### The premise: every promotion moves the file, and nothing outside this repo notices

A `git mv` changes a file's path. Every reader who holds a path to a `roadmaps/<category>/BE-NNNN-…`
item — a Markdown link, a GitHub blob URL, a bookmark — points at nothing the moment that item's
`Status` next changes, unless something rewrites that specific reader. This repository already
works around the fallout in two places: `promote_roadmap_items.py` rewrites cross-links **within**
`roadmaps/`, and [BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md)
extended that to `docs/` (plus a gate check so a missed one fails `make check`). Both are real,
working repairs — for the two link surfaces this repository can see and rewrite at HEAD.

### A concrete, currently-live bug the existing repairs don't reach

[`scripts/sync_roadmap_tracking_issues.py`](../../scripts/sync_roadmap_tracking_issues.py)
(BE-0109) opens a GitHub tracking issue for every open item, with a body that links back to the
item's file:

```python
href = f"{REPO_BLOB_ROOT}/roadmaps/{item.category}/{stem}/{stem}.md"
```

`create_issue()` sets this body **once**, at creation time. The sync script's other action on an
existing issue is `close_issue()` — nothing ever calls an update/edit on an already-open issue's
body. So the instant an item is promoted from `Proposal` to `In progress` (its `Status` changes,
its issue stays open because both are "open" statuses, its `category` segment changes from
`proposals` to `in-progress`), the link embedded in that issue's body 404s — and stays broken for
as long as the issue is open, then forever after it closes, since a closed issue is never revisited
either. Given that almost every item passes through `Proposal`, and many also pass through
`In progress`, before `Implemented`, this is not an edge case: it is the **default outcome** for the
tracking-issue link on any item that is promoted at all, and BE-0096's docs/roadmaps repair — scoped
explicitly to those two surfaces — does not and structurally cannot reach it (it is a GitHub API
object, not a file in the tree).

### The repair-every-reader strategy has no ceiling

Tracking issues are one instance of a broader pattern: every additional *consumer* of a roadmap
item's path is a new place that can go stale, and "detect drift, then auto-repair or gate-check it"
only covers the consumers this repository's tooling can see and rewrite in its own commits. It
cannot reach, and never will:

- **External references** — a link pasted into a Slack thread, cited from a blog post, indexed by a
  search engine, or left in a comment on a *different* GitHub repository — all point at a specific
  path at `main` and break the moment that path moves, permanently, with no mechanism in this repo
  able to touch them.
- **This repo's own closed artifacts** — a merged PR description, an old commit message, or (as
  above) an already-closed tracking issue — nothing revisits these after the fact even though they
  are technically "in GitHub," because nothing in the promotion path is wired to edit them.

Each newly discovered consumer (tracking issues today; the next one tomorrow) becomes a bespoke
special case bolted onto `roadmap-promote`. That is the opposite of the "make the structure carry
the invariant" principle the contributor-workflow line otherwise follows
([BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md),
[BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)):
a path that keeps moving *is* the invariant violation, and chasing every reader of it is treating the
symptom. `docs/ai-development.md` already documents the docs/-side repair as "the same self-healing
the index gets" — i.e., it already concedes rot is the expected consequence of the folder scheme and
budgets tooling to chase it. This item removes the underlying cause instead.

### What BE-0078 actually needed, versus what it built

BE-0078's real complaint was legitimate: `proposals/` was flattening three states (live proposal,
in-progress, deferred) that `Status` already distinguished, so a reader browsing the folder — on
disk or via the index — couldn't tell them apart without opening each file. But a four-way
**index/dashboard grouping** and a four-way **filesystem layout** are two different things that
BE-0078 bundled into one change. `build_roadmap_index.py` already derives each item's bucket from
its `Status` field, not from which folder the file physically sits in — the generated tables and the
GitHub Pages dashboard ([BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md))
give a reader the exact at-a-glance distinction BE-0078 wanted, with no directory move required to
keep it accurate. Decoupling the two removes the promotion-triggered rot while keeping the one thing
BE-0078 actually needed.

## Detailed design

MECE work breakdown, all touching the tooling BE-0078 introduced and BE-0096/BE-0149 extended:

1. **One flat directory.** Every item lives at `roadmaps/BE-NNNN-<slug>/` (or `BE-0159-<slug>/`
   while unallocated); the `{implemented,in-progress,proposals,deferred}/` segment is dropped. An
   item's path becomes permanent the moment its ID is allocated
   ([BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md))
   and never changes again — the same "permanent, never renumbered" guarantee the roadmap already
   gives the ID itself, extended to the path.
2. **`scripts/build_roadmap_index.py`.** Keeps deriving the Implemented / In progress / Proposals /
   Deferred × Topic tables from `Status` exactly as today — only the generated link path drops the
   category segment. `CATEGORIES` (a directory-scan concern) collapses to scanning one directory;
   `SECTIONS`/bucketing (a `Status`-classification concern) is unchanged.
3. **`scripts/promote_roadmap_items.py` and `roadmap-promote.yml`.** The `git mv` / "misfiled
   directory" concept disappears — there is no folder an item's `Status` can disagree with anymore.
   What is left of this script's job — regenerating the index after a `Status` edit — is already
   what `make roadmap-index` does on its own, so the promote step shrinks to "rebuild the index and
   commit if it changed," foldable into the existing `roadmap-id` workflow rather than kept as a
   separate one.
4. **`scripts/allocate_roadmap_ids.py`.** The four-category scan (`CATEGORIES`, used for both
   used-ID counting and placeholder discovery) collapses to scanning the one directory;
   `PLACEHOLDER_CATEGORY` is no longer meaningful (there is only one place to look).
5. **`scripts/check_roadmap_format.py` / `scripts/roadmap_ids.py` (BE-0149).** The shared
   `is_item_dir` / `is_numbered_dir` / `is_placeholder_dir` predicates are unaffected — they already
   test only the directory *name*, never its parent. `_items()`'s walk over `CATEGORIES` collapses
   to walking one directory: a net simplification of the exact surface BE-0149 just finished
   hardening against drift, removing one whole axis (which of four folders) that hardening had to
   reason about.
6. **`scripts/new_roadmap_item.py`.** Scaffolds every new item directly under `roadmaps/`,
   regardless of `--status`; the flag still sets the metadata `Status` (and thus the eventual index
   bucket), it just no longer selects a folder.
7. **`scripts/sync_roadmap_tracking_issues.py` (BE-0109) — the concrete fix.** `issue_body()`'s
   `href` drops the `item.category` segment, so a tracking issue's link is stable across every
   future `Status` change for the rest of that item's life — closing the live bug described above.
   As a one-time migration (not a recurring mechanism), a follow-up pass over every existing
   tracking issue (`gh issue list --label roadmap-tracking --state all`, then `gh issue edit` each
   body) repairs the links that are already broken today, rather than leaving them broken forever.
8. **Prose.** `CLAUDE.md`, `roadmaps/README.md` / `README-ja.md`, and `docs/ai-development.md` drop
   the "`Status` maps to one of four folders, a bijection" rule and its four-row table, replacing it
   with "every item lives in `roadmaps/BE-NNNN-<slug>/`; `Status` decides only the index/dashboard
   bucket." The four index buckets themselves, their order, and the "In progress" naming
   (BE-0078) are untouched — only the folder-equals-bucket claim is retracted.
9. **One-time migration.** A single PR `git mv`s every existing item's directory to the flat
   layout and regenerates the index; this is the one deliberate, one-time breakage this item
   accepts, in exchange for removing an unbounded number of future ones (see *Alternatives*).
10. **Downstream items.** [BE-0154](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha.md)
    hardens `roadmap-promote.yml`'s checkout of `promote_roadmap_items.py`; once that script's job
    shrinks to a reindex (item 3), BE-0154's premise — a PR-influenced script running under
    `contents: write` — mostly evaporates, so BE-0154 should be revisited (closed or rewritten
    against whatever remains) once this item lands, rather than implemented against code this item
    retires.

### Performance and scale

A fair question is whether regenerating the index (and syncing tracking issues) on a `Status` change
grows costly as the item count climbs. It does not, for three reasons, and this item does not make it
worse:

- **The regeneration cost already exists today; this item removes work, not adds it.** The index is
  *already* rebuilt from scratch on every roadmap change — `roadmap-promote` runs
  `build_roadmap_index.py` after each move. What this item removes is the per-promotion `git mv`; the
  reindex that remains is the same one that runs now. A status change goes from "move the directory
  **and** reindex" to "reindex only," so the per-change work strictly shrinks.
- **The rebuild is a cheap O(N) pass.** Both the index (`build_roadmap_index.py`) and the dashboard
  (`build_roadmap_dashboard.py`) are a single linear scan that reads each item's small metadata block
  and re-emits the tables. At the current 151 items the actual work is on the order of tens of
  milliseconds (the wall-clock is dominated by Python startup, not the scan); a 10× larger roadmap is
  still well under a second. The gate already runs a full index build on every `make check`, so this
  cost is exercised continuously and is not a regression risk.
- **Tracking-issue sync is diff-based, not a full regeneration, so it does not grow with the total
  item count.** `sync_roadmap_tracking_issues.py`'s `plan()` computes only the delta (`to_create` /
  `to_close`) and touches just the issues that changed; a single promotion is typically zero or one
  API write. The only quantity that scales with the roadmap is one local file scan (cheap) and one
  `gh issue list` call bounded by the *open*-issue count (`--limit 1000`), neither of which this item
  changes.

### Prime-directive compliance

Documentation, roadmap-tooling scripts, and their gate tests only. No LLM enters any path; `run` and
CI stay deterministic; nothing app- or backend-specific is touched.

## Alternatives considered

- **Keep four folders; keep extending detect-and-repair to every newly discovered consumer** (the
  path already being walked: `promote_roadmap_items.py` for `roadmaps/`, BE-0096 for `docs/`, this
  item's own fix for tracking issues). Rejected as the long-term answer: the list of consumers is
  unbounded and this repository cannot see, let alone repair, the ones outside it (external
  bookmarks, search-engine caches, other repositories' issues/PRs, already-closed artifacts of this
  one). The tracking-issue bug shows a repo-*internal* consumer still slipped through even with
  BE-0096 already shipped — the strategy has no ceiling by construction.
- **Keep the four folders, but leave a redirect/alias at the old path when an item moves** (e.g. a
  stub file forwarding to the new location). Rejected: git has no first-class alias/redirect
  primitive, so simulating one is bespoke machinery roughly as involved as flattening, while still
  leaving a filesystem layout that implies "current state" without actually being reliably current.
- **A different folder count** (e.g. fold `deferred/` back into `proposals/`, three folders instead
  of four). Doesn't address the actual problem: any folder keyed off a mutable `Status` still moves
  a file on every status change; the number of folders is orthogonal to path stability.
- **Do nothing; accept the rot as a documented, bounded cost.** Close to the status quo —
  `docs/ai-development.md` already frames the docs/ repair as needed "self-healing," implicitly
  conceding rot is expected. Rejected because the tracking-issue bug shows the cost is not bounded to
  the two surfaces already patched, and it recurs on every future promotion of every currently open
  item, with no repair in place today.
- **Incrementally patch the index/dashboard on each change instead of the current full rebuild** (to
  pre-empt any growth in reindex time as the item count climbs). Rejected, and out of scope for this
  item: a full rebuild from each item's metadata is precisely the single-source-of-truth guarantee
  the project already relies on ([BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md),
  [BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md)) —
  an incremental patch reintroduces a stateful drift surface for a speed the roadmap does not need
  (the O(N) rebuild is tens of milliseconds at today's scale; see *Performance and scale*). This item
  neither adds nor removes that rebuild, so if incremental generation is ever wanted it is an
  orthogonal follow-up, not a reason to keep the folders.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

> **Delivered in two batches** so each PR stays independently reviewable (under the reviewer's
> per-PR file-count limit): PR1 introduces a transitional dual-layout tooling that scans both the
> flat root and the legacy status folders, and flattens the Implemented items; PR2 flattens the rest,
> removes the dual-layout code, and does the flat-only cleanup (retire `promote`, Status-based
> approvals gate, prose retraction). The tree is valid at every step.

- [ ] Flatten the physical layout: every item `git mv`'d to the flat root (PR1: Implemented items;
      PR2: the remainder), index regenerated at the new (flat) paths.
- [ ] `scripts/build_roadmap_index.py` — drop the per-category directory from link construction;
      keep `Status`-derived bucket grouping in the generated tables unchanged.
- [ ] `scripts/promote_roadmap_items.py` / `roadmap-promote.yml` — retire the file-move logic; fold
      the remaining "reindex on Status change" into the existing `roadmap-id` workflow (or keep it
      minimal and separate).
- [ ] `scripts/allocate_roadmap_ids.py` — collapse the four-category scan to one directory scan.
- [ ] `scripts/check_roadmap_format.py` / `scripts/roadmap_ids.py` — collapse `_items()`'s
      four-folder walk to one directory (the id-shape predicates themselves are unaffected).
- [ ] `scripts/new_roadmap_item.py` — scaffold every new item directly under `roadmaps/`,
      independent of `--status`.
- [ ] `scripts/sync_roadmap_tracking_issues.py` — drop `item.category` from `issue_body()`'s
      `href`; run a one-time repair pass over every existing tracking issue (open and closed) to fix
      already-broken links.
- [ ] Prose — `CLAUDE.md`, `roadmaps/README.md` / `README-ja.md`, `docs/ai-development.md`: replace
      the folder-bijection rule with "one directory; `Status` decides only the index bucket."
- [ ] Flag [BE-0154](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha.md)
      for revision or closing once `promote_roadmap_items.py`'s job shrinks to a reindex.

Log:

- PR1 (batch 1), [#628](https://github.com/bajutsu-e2e/bajutsu/pull/628): added the transitional
  dual-layout walk (`roadmap_ids.iter_item_dirs` scans the flat root and the legacy folders) across
  the index / allocate / format / sync / dashboard / lint tooling, scaffolded new items flat, and
  `git mv`'d the Implemented items to the flat root (regenerating the index and repairing the
  cross-links). `promote` still manages the remaining foldered items. BE-0159 moved to `In progress`.
- PR2 (batch 2), [#631](https://github.com/bajutsu-e2e/bajutsu/pull/631): `git mv`'d the remaining items (proposals / in-progress / deferred) to the flat root,
  removed the transitional dual-layout code (`iter_item_dirs` is a single flat scan again, `category`
  dropped), retired `promote_roadmap_items.py` + its workflow / gate test / Make target, switched the
  proposal-approvals gate to Status-based detection, and completed the prose retraction across
  `CLAUDE.md` / the READMEs / `docs/ai-development.md` / `docs/roadmap-workflow.md` / the skills.
  BE-0159 → `Implemented`; BE-0154 flagged. Follow-up: the one-time `gh issue edit` repair of existing
  tracking-issue links runs after merge (the flat paths only resolve once this lands on `main`).

## References

- [BE-0078 — Status-driven roadmap folders](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md)
  — the item this one narrows: keeps its index-bucket idea, retracts its folder-per-status one.
- [BE-0096 — Keep docs links to roadmap items from rotting on promotion](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md)
  — the detect-and-repair approach this item replaces with a structural fix for the `roadmaps/` and
  `docs/` link surfaces; its gate-check *detector* stays useful for catching plain typos independent
  of promotion.
- [BE-0109 — GitHub Issues as the ownership tracker for open roadmap items](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md)
  — the tracking-issue mechanism whose `issue_body()` link breaks on every promotion today; the
  concrete bug motivating this item.
- [BE-0094 — Generated roadmap status dashboard](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md)
  — evidence that the four-way grouping already works from `Status` alone, with no folder needed.
- [BE-0149 — Close the roadmap-placeholder format-guardrail gap](../BE-0149-roadmap-placeholder-format-guardrail/BE-0149-roadmap-placeholder-format-guardrail.md)
  — the shared `scripts/roadmap_ids.py` predicate module and `scripts/check_roadmap_format.py` this
  item's format-check change builds on.
- [BE-0154 — Run roadmap-promote from the base SHA](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha.md)
  — a proposal whose target script this item retires; should be revised or closed once this lands.
- [`scripts/sync_roadmap_tracking_issues.py`](../../scripts/sync_roadmap_tracking_issues.py),
  [`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py),
  [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py),
  [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py),
  [`scripts/new_roadmap_item.py`](../../scripts/new_roadmap_item.py),
  [`scripts/check_roadmap_format.py`](../../scripts/check_roadmap_format.py),
  [`scripts/roadmap_ids.py`](../../scripts/roadmap_ids.py) — the tooling surface this item touches.

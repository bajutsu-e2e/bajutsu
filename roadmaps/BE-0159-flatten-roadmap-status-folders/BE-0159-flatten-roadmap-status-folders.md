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

[BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md) gave each of the four
`Status` values its own directory (`roadmaps/{implemented,in-progress,proposals,deferred}/`). A
promotion `git mv`s an item's directory every time its `Status` changes.

This item retires that half of BE-0078. It flattens every item into a single
`roadmaps/BE-NNNN-<slug>/`. An item's path is then fixed forever from the moment its ID is allocated
([BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)), and a
promotion never touches the filesystem again.

`Status` keeps deciding the generated index and dashboard bucket. The reader-facing distinction that
BE-0078 wanted stays as it is. `Status` stops deciding the file's location, which is the part
that rots links.

## Motivation

### The premise: every promotion moves the file, and nothing outside this repo notices

A `git mv` changes a file's path. Every reader who holds a path to a `roadmaps/<category>/BE-NNNN-…`
item points at nothing the moment that item's `Status` next changes, unless something rewrites that
specific reader. The path may sit in a Markdown link, a GitHub blob URL, or a bookmark.

This repository already works around the fallout in two places:

- `promote_roadmap_items.py` rewrites cross-links **within** `roadmaps/`.
- [BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md) extended
  that repair to `docs/`, and added a gate check so a missed link fails `make check`.

Both are real, working repairs for the two link surfaces that this repository can see and rewrite at
HEAD.

### A concrete, currently-live bug the existing repairs don't reach

[`scripts/sync_roadmap_tracking_issues.py`](../../scripts/sync_roadmap_tracking_issues.py) (BE-0109)
opens a GitHub tracking issue for every open item. The issue body links back to the item's file:

```python
href = f"{REPO_BLOB_ROOT}/roadmaps/{item.category}/{stem}/{stem}.md"
```

`create_issue()` sets this body **once**, at creation time. The sync script's one other action on an
existing issue is `close_issue()`. Nothing ever edits the body of an already-open issue.

Consider an item promoted from `Proposal` to `In progress`. Its `Status` changes, and its
`category` segment changes from `proposals` to `in-progress`. Its issue stays open, because both are
"open" statuses. The link embedded in that issue's body now returns 404. The link stays broken while
the issue is open, and forever after the issue closes, since nobody revisits a closed issue either.

Almost every item passes through `Proposal`, and many also pass through `In progress`, before
`Implemented`. This is not an edge case. For the tracking-issue link on
any item that is promoted at all, breakage is the **default outcome**. BE-0096's docs/roadmaps repair is scoped explicitly to those two
surfaces. It does not reach this link and structurally cannot, because the link lives in a GitHub API
object and not in a file in the tree.

### The repair-every-reader strategy has no ceiling

Tracking issues are one instance of a broader pattern. Every additional *consumer* of a roadmap
item's path is a new place that can go stale. The strategy of "detect drift, then auto-repair or
gate-check it" covers only the consumers that this repository's tooling can see and rewrite in its
own commits. It cannot reach the following consumers, and never will:

- **External references.** A link pasted into a Slack thread, cited from a blog post, indexed by a
  search engine, or left in a comment on a *different* GitHub repository points at a specific path
  at `main`. It breaks the moment that path moves. It breaks permanently, and no mechanism in this
  repo can touch it.
- **This repo's own closed artifacts.** A merged PR description, an old commit message, or an
  already-closed tracking issue (as above) lives in GitHub, technically. Nothing in the promotion
  path is wired to edit it, so nothing revisits it.

Each newly discovered consumer (tracking issues today, the next one tomorrow) becomes a bespoke
special case bolted onto `roadmap-promote`. That is the opposite of the principle "make the structure
carry the invariant", which the contributor-workflow line otherwise follows
([BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md),
[BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)). A path that
keeps moving *is* the invariant violation, and chasing every reader of it treats the symptom.

`docs/ai-development.md` already documents the docs/-side repair as "the same self-healing the index
gets". That wording concedes that rot is the expected consequence of the folder scheme, and it
budgets tooling to chase the rot. This item removes the underlying cause instead.

### What BE-0078 actually needed, versus what it built

BE-0078's real complaint was legitimate. `proposals/` flattened three states (live proposal,
in-progress, deferred) that `Status` already distinguished. A reader browsing the folder, on disk or
via the index, could not tell the states apart without opening each file.

But a four-way **index/dashboard grouping** and a four-way **filesystem layout** are two different
things, and BE-0078 bundled them into one change. `build_roadmap_index.py` already derives each
item's bucket from its `Status` field, not from the folder where the file physically sits. The
generated tables and the GitHub Pages dashboard
([BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md)) give a reader the
at-a-glance distinction that BE-0078 wanted, with no directory move to keep it accurate. Decoupling
the two removes the promotion-triggered rot and keeps the one thing BE-0078 needed.

## Detailed design

The work breakdown is MECE. Every unit touches the tooling that BE-0078 introduced and that
BE-0096 and BE-0149 extended.

1. **One flat directory.** Every item lives at `roadmaps/BE-NNNN-<slug>/` (or `BE-0159-<slug>/`
   while unallocated). The `{implemented,in-progress,proposals,deferred}/` segment is dropped. An
   item's path becomes permanent the moment its ID is allocated
   ([BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)) and
   never changes again. The roadmap already gives the ID this "permanent, never renumbered"
   guarantee, and the guarantee now extends to the path.
2. **`scripts/build_roadmap_index.py`.** The script keeps deriving the Implemented / In progress /
   Proposals / Deferred × Topic tables from `Status`, as today. The generated link path
   drops the category segment, and nothing else changes. `CATEGORIES` (a directory-scan concern)
   collapses to scanning one directory. `SECTIONS`/bucketing (a `Status`-classification concern) is
   unchanged.
3. **`scripts/promote_roadmap_items.py` and `roadmap-promote.yml`.** The `git mv` and "misfiled
   directory" concept disappears, because no folder exists that an item's `Status` can disagree
   with. The script's remaining job is regenerating the index after a `Status` edit, which
   `make roadmap-index` already does on its own. The promote step shrinks to "rebuild the
   index and commit if it changed". The existing `roadmap-id` workflow can absorb that step, so it
   no longer needs a separate workflow.
4. **`scripts/allocate_roadmap_ids.py`.** The four-category scan (`CATEGORIES`, used for both
   used-ID counting and placeholder discovery) collapses to scanning the one directory.
   `PLACEHOLDER_CATEGORY` loses its meaning, because one place remains to look.
5. **`scripts/check_roadmap_format.py` / `scripts/roadmap_ids.py` (BE-0149).** The shared
   `is_item_dir` / `is_numbered_dir` / `is_placeholder_dir` predicates are unaffected. They already
   test the directory *name* and never its parent. The walk over `CATEGORIES` in `_items()` collapses
   to walking one directory. This is a net simplification of the surface that BE-0149 just finished
   hardening against drift, and it removes one whole axis (which of four folders) that the hardening
   had to reason about.
6. **`scripts/new_roadmap_item.py`.** The script scaffolds every new item directly under
   `roadmaps/`, regardless of `--status`. The flag still sets the metadata `Status`, and thus the
   eventual index bucket. It no longer selects a folder.
7. **`scripts/sync_roadmap_tracking_issues.py` (BE-0109), the concrete fix.** The `href` in
   `issue_body()` drops the `item.category` segment. A tracking issue's link is then stable across
   every future `Status` change for the rest of that item's life, which closes the live bug described
   above. A one-time migration (not a recurring mechanism) repairs the links that are already broken
   today, instead of leaving them broken forever. The migration is a follow-up pass over every
   existing tracking issue: `gh issue list --label roadmap-tracking --state all`, then `gh issue
   edit` on each body.
8. **Prose.** `CLAUDE.md`, `roadmaps/README.md` / `README-ja.md`, and `docs/ai-development.md` drop
   the rule "`Status` maps to one of four folders, a bijection" and its four-row table. They replace
   it with "every item lives in `roadmaps/BE-NNNN-<slug>/`; `Status` decides only the index/dashboard
   bucket." The four index buckets, their order, and the "In progress" naming (BE-0078) are
   untouched. The change retracts the folder-equals-bucket claim and nothing else.
9. **One-time migration.** A single PR `git mv`s every existing item's directory to the flat layout
   and regenerates the index. This is the one deliberate, one-time breakage that this item accepts.
   In exchange, it removes an unbounded number of future breakages (see *Alternatives*).
10. **Downstream items.** [BE-0154](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha.md)
    hardens how `roadmap-promote.yml` checks out `promote_roadmap_items.py`. Once that script's job
    shrinks to a reindex (unit 3), BE-0154's premise mostly evaporates. Its premise is a
    PR-influenced script running under `contents: write`. BE-0154 should be revisited once this item
    lands, either closed or rewritten against whatever remains. Implementing it against code that
    this item retires would waste the work.

### Performance and scale

A fair question is whether regenerating the index (and syncing tracking issues) on a `Status` change
grows costly as the item count climbs. It does not, for three reasons, and this item does not make it
worse:

- **The regeneration cost already exists today, and this item removes work.** The index is
  *already* rebuilt from scratch on every roadmap change: `roadmap-promote` runs
  `build_roadmap_index.py` after each move. This item removes the per-promotion `git mv`, and the
  remaining reindex is the same one that runs now. A status change goes from "move the directory
  **and** reindex" to "reindex", so the per-change work strictly shrinks.
- **The rebuild is a cheap O(N) pass.** The index (`build_roadmap_index.py`) and the dashboard
  (`build_roadmap_dashboard.py`) each make a single linear scan. The scan reads each item's small
  metadata block and re-emits the tables. At the current 151 items, the actual work takes tens of
  milliseconds, and Python startup dominates the wall-clock. A roadmap 10× larger would still take
  well under a second. The gate already runs a full index build on every `make check`, so this cost
  is exercised continuously and poses no regression risk.
- **Tracking-issue sync is diff-based, not a full regeneration, so it does not grow with the total
  item count.** `plan()` in `sync_roadmap_tracking_issues.py` computes the delta (`to_create` /
  `to_close`) and touches the issues that changed. A single promotion typically costs zero or one
  API write. Two quantities scale with the roadmap: one local file scan (cheap) and one
  `gh issue list` call bounded by the *open*-issue count (`--limit 1000`). This item changes
  neither.

### Prime-directive compliance

This item touches documentation, roadmap-tooling scripts, and their gate tests. No LLM enters any
path, `run` and CI stay deterministic, and nothing app- or backend-specific is touched.

## Alternatives considered

- **Keep four folders, and keep extending detect-and-repair to every newly discovered consumer.**
  This is the path already being walked: `promote_roadmap_items.py` for `roadmaps/`, BE-0096 for
  `docs/`, and this item's own fix for tracking issues. Rejected as the long-term answer. The list of
  consumers is unbounded, and this repository cannot see, let alone repair, the ones outside it:
  external bookmarks, search-engine caches, other repositories' issues and PRs, and already-closed
  artifacts of this repository. The tracking-issue bug shows that a repo-*internal* consumer still
  slipped through even with BE-0096 shipped. By construction, the strategy has no ceiling.
- **Keep the four folders, but leave a redirect or alias at the old path when an item moves** (for
  example, a stub file forwarding to the new location). Rejected. Git has no first-class
  alias/redirect primitive, so simulating one is bespoke machinery about as involved as flattening.
  The result would still be a filesystem layout that implies "current state" without being reliably
  current.
- **A different folder count** (for example, fold `deferred/` back into `proposals/`, for three
  folders instead of four). This does not address the actual problem. Any folder keyed off a mutable
  `Status` still moves a file on every status change, so the number of folders is orthogonal to path
  stability.
- **Do nothing, and accept the rot as a documented, bounded cost.** This is close to the status quo:
  `docs/ai-development.md` already frames the docs/ repair as needed "self-healing", which
  implicitly concedes that rot is expected. Rejected, because the tracking-issue bug shows the cost
  is not bounded to the two surfaces already patched. It recurs on every future promotion of every
  currently open item, with no repair in place today.
- **Incrementally patch the index/dashboard on each change, instead of the current full rebuild**
  (to pre-empt any growth in reindex time as the item count climbs). Rejected, and out of scope for
  this item. A full rebuild from each item's metadata is the single-source-of-truth guarantee that
  the project already relies on
  ([BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md),
  [BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md)). An incremental
  patch reintroduces a stateful drift surface for a speed the roadmap does not need. The O(N)
  rebuild takes tens of milliseconds at today's scale (see *Performance and scale*). This item
  neither adds nor removes that rebuild. If incremental generation is ever wanted, it is an
  orthogonal follow-up and not a reason to keep the folders.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

> **Delivered in two batches** so each PR stays independently reviewable (under the reviewer's
> per-PR file-count limit): PR1 introduces a transitional dual-layout tooling that scans both the
> flat root and the legacy status folders, and flattens the Implemented items; PR2 flattens the rest,
> removes the dual-layout code, and does the flat-only cleanup (retire `promote`, Status-based
> approvals gate, prose retraction). The tree is valid at every step.

- [x] Flatten the physical layout: every item `git mv`'d to the flat root (PR1: Implemented items;
      PR2: the remainder), index regenerated at the new (flat) paths.
- [x] `scripts/build_roadmap_index.py` — drop the per-category directory from link construction;
      keep `Status`-derived bucket grouping in the generated tables unchanged.
- [x] `scripts/promote_roadmap_items.py` / `roadmap-promote.yml` — retire the file-move logic; fold
      the remaining "reindex on Status change" into the existing `roadmap-id` workflow (or keep it
      minimal and separate).
- [x] `scripts/allocate_roadmap_ids.py` — collapse the four-category scan to one directory scan.
- [x] `scripts/check_roadmap_format.py` / `scripts/roadmap_ids.py` — collapse `_items()`'s
      four-folder walk to one directory (the id-shape predicates themselves are unaffected).
- [x] `scripts/new_roadmap_item.py` — scaffold every new item directly under `roadmaps/`,
      independent of `--status`.
- [x] `scripts/sync_roadmap_tracking_issues.py` — drop `item.category` from `issue_body()`'s
      `href`; run a one-time repair pass over every existing tracking issue (open and closed) to fix
      already-broken links.
- [x] Prose — `CLAUDE.md`, `roadmaps/README.md` / `README-ja.md`, `docs/ai-development.md`: replace
      the folder-bijection rule with "one directory; `Status` decides only the index bucket."
- [x] Flag [BE-0154](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha.md)
      for revision or closing once `promote_roadmap_items.py`'s job shrinks to a reindex.

Log:

- PR1 (batch 1), [#628](https://github.com/bajutsu-e2e/bajutsu/pull/628):
  - Added the transitional dual-layout walk (`roadmap_ids.iter_item_dirs` scans the flat root and
    the legacy folders) across the index / allocate / format / sync / dashboard / lint tooling.
  - Scaffolded new items flat.
  - `git mv`'d the Implemented items to the flat root, regenerating the index and repairing the
    cross-links.
  - `promote` still manages the remaining foldered items. BE-0159 moved to `In progress`.
- PR2 (batch 2), [#631](https://github.com/bajutsu-e2e/bajutsu/pull/631):
  - `git mv`'d the remaining items (proposals / in-progress / deferred) to the flat root.
  - Removed the transitional dual-layout code (`iter_item_dirs` is a single flat scan again, and
    `category` is dropped).
  - Retired `promote_roadmap_items.py` with its workflow, gate test, and Make target.
  - Switched the proposal-approvals gate to Status-based detection.
  - Completed the prose retraction across `CLAUDE.md`, the READMEs, `docs/ai-development.md`,
    `docs/roadmap-workflow.md`, and the skills.
  - BE-0159 → `Implemented`; BE-0154 flagged.
  - Follow-up: the one-time `gh issue edit` repair of existing tracking-issue links runs after
    merge. The flat paths resolve only once this lands on `main`.

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

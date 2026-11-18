**English** · [日本語](BE-0156-roadmap-topic-label-sync-ja.md)

# BE-0156 — Keep roadmap-item PR labels in sync with Topic

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0156](BE-0156-roadmap-topic-label-sync.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0156") |
| Implementing PR | [#612](https://github.com/bajutsu-e2e/bajutsu/pull/612), [#817](https://github.com/bajutsu-e2e/bajutsu/pull/817) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This item adds a GitHub Actions workflow that keeps each open roadmap item's PR labeled with a
`topic:<key>` label matching its current `Topic` metadata field. It runs automatically, with no
author or reviewer action. It covers two cases:

- A PR that adds a brand new item gets its topic label.
- A PR that changes the `Topic` on an already-numbered item (in any status other than
  `Implemented`) gets relabeled to match.

## Motivation

The roadmap already groups every item under one of 23 topics
([`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py)'s `TOPICS`
tuple — e.g. "Backend expansion (iOS actuators)", "Integration & automation",
"Security hardening"). The index pages render items grouped by topic. But the grouping only becomes
visible once someone opens the item file and reads its metadata block, or after the next index
regeneration. The GitHub PR list is where reviewers triage which items to look at. There, a PR
carries no topic signal beyond its title.

A `topic:<key>` label surfaces that grouping immediately in the PR list and in notifications. A
reviewer who owns "Security hardening" or "codegen coverage" can filter to the PRs that concern
them without opening each diff. The label also gives a free, queryable history:
`is:pr label:topic:mcp` finds every PR ever raised for a topic, open or closed.

A `Topic` is not fixed at proposal time. Reclassifying an item is a normal part of shaping the
roadmap. Examples are moving an item to a better-fitting topic and splitting a catch-all topic once
it grows. Reclassification happens on already-numbered items at any `Status` short of
`Implemented`, not just on brand-new proposals.

Suppose labeling ran once, at PR-open time. A PR whose `Topic` changes mid-review would then carry a
stale label for the rest of its life. The stale label would mislead the same triage the label exists
to help. The labeling has to track edits, not just additions.

`Implemented` items are out of scope for the relabel side. Once an item has shipped, no open PR
remains to triage by topic. Further prose edits to a shipped item's file are not the kind of
routing decision this label exists to surface. This mirrors the open/shipped boundary that
[`roadmap-tracking-issues.yml`](../../.github/workflows/roadmap-tracking-issues.yml) (BE-0109)
already draws. That workflow keeps a tracking issue open for `Approved` / `In progress` items and
closes it once an item ships. It never touches `Implemented` items.

The workflow is pure PR triage tooling. It never influences `run`, the deterministic gate, or any
pass/fail verdict (prime directive 1), and it touches no per-app behavior (prime directive 3).

## Detailed design

The work is three independent pieces.

1. **Topic → label mapping, reusing the existing canonical topic list.** The 23 `(name, key,
   has_origin)` tuples in `TOPICS`
   ([`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py)) are
   the single source of truth. They map a human-readable `Topic` value (as it appears in a BE
   item's metadata block) to a short, stable key (e.g. `"Backend expansion (iOS actuators)"` →
   `backend`). The label name is `topic:<key>` (e.g. `topic:backend`, `topic:mcp`,
   `topic:security`). The name is short enough to scan in the PR list's label row. It reuses the
   key the index already uses, so adding a 24th topic never needs a separate label-mapping update.

2. **A script that turns "how this PR changed roadmap items" into "labels to add / remove".**
   A new `scripts/sync_roadmap_topic_labels.py` is **reconciling, not a diff replay**. Its input is
   the PR's changed-file entries: path, status, and — for a rename — the previous path. It restricts
   them to English item files (the non-`-ja` file per item directory) in the flat
   `roadmaps/BE-NNNN-<slug>/` tree (BE-0159 retired the per-`Status` folders). It then computes the
   PR's **desired** `topic:<key>` set. It skips an item whose head `Status` is `Implemented`,
   because a shipped item has no open PR left to triage. No `implemented/` folder exists to key on,
   so the script reads the item's `Status` from the same head content. The cases are:
   - **Added** (a brand new item): read its `Topic` from the working tree (the PR head). Its
     label is desired.
   - **Modified or renamed** (an edit to an already-numbered item, including a slug rename): read
     the current `Topic` from the working tree. Read the previous `Topic` from the base commit with
     `git show <base-sha>:<old-path>`. The old path is the current path unless the entry is a
     rename. Both reads reuse the `<!-- BE-METADATA -->` parsing (`META_BLOCK_RE` /
     `META_ROW_RE`) already in `build_roadmap_index.py`, not a reimplementation. If the two `Topic`
     values are equal, the item contributes nothing. This is the common case, because most edits do
     not touch `Topic`. A prose-only edit never labels a PR on its own, and a `Status` change alone
     is likewise not a trigger. If the values differ, the **new** topic's label is desired.
   - A `Topic` value absent from `TOPIC_KEY_BY_NAME` (an item authored by hand, bypassing
     `make new-roadmap-item`'s validation) is not an error. The script emits a `::warning::` line
     for that file and leaves it out of the desired set. A labeling gap never blocks or fails the
     PR.

   The script then **reconciles** the desired set against the PR's *current* `topic:*` labels,
   which the workflow passes in. It prints one `add <label>` line per desired-but-absent label. It
   prints one `remove <label>` line per present-but-no-longer-desired label. GitHub's
   `pulls/{pr}/files` is the whole base→head diff. The desired set is thus a pure function of
   the current head, and every push recomputes it in full. Reconciliation **converges** where a
   naive "remove old, add new" delta would not (see *Alternatives considered*).

3. **A workflow, `.github/workflows/roadmap-topic-labels.yml`.** It triggers on `pull_request`
   (defaulting to `opened` / `reopened` / `synchronize`), path-filtered to the whole flat
   `roadmaps/**` tree. The script narrows the input to English item files and skips `Implemented`
   ones (see *Motivation*), so no folder-level path filter is needed. The workflow skips fork PRs,
   whose token is read-only. Its steps are:
   - Check out the PR **head** (`ref: <head.sha>`), not the default `pull_request` merge ref. The
     changed-file list and the base comparison are both relative to the head. The working tree that
     the script reads each item's current `Topic` from must be the head, not head-merged-onto-base.
   - Fetch the PR's base commit
     (`git fetch --depth=1 origin ${{ github.event.pull_request.base.sha }}`), so `git show` can
     read each modified/renamed file's previous content. This step is **non-fatal**. Suppose the
     base SHA is unreachable, for example because the base branch was force-pushed after the run
     queued. The job then warns and exits green instead of red. Pure triage tooling never gates a
     PR.
   - List the PR's changed files via `gh api pulls/{pr}/files` (`status`, `filename`,
     `previous_filename`). Read the PR's current labels via `gh pr view --json labels`. Feed both to
     `scripts/sync_roadmap_topic_labels.py` to get the add/remove actions.
   - If the script prints nothing, exit green as a no-op. That happens for a PR touching just the
     generated index, or one already in sync. `roadmap-proposal-approvals.yml` and
     `roadmap-tracking-issues.yml` already follow the same discipline for out-of-scope PRs.
   - For each *add* action's label, run `gh label create <name> --color <fixed-color> --force`. The
     command is idempotent: it creates the label on first use of a topic and updates it harmlessly
     if it already exists. A brand new topic never needs a manual label-creation step. Every
     `topic:*` label shares one fixed color, so the labels read as a family distinct from `bug` /
     `documentation` / etc.
   - Apply the add/remove labels in **two** `gh pr edit` calls (one `--add-label`, one
     `--remove-label`), not one round-trip per label. The remove is best-effort. A label already
     absent (e.g. a maintainer removed it by hand) is not an error.
   - The workflow needs these permissions:
     - `contents: read` (checkout and `git show` against the base commit).
     - `pull-requests: write` (list files, read/edit labels).
     - `issues: write` (label creation lives under the Issues REST surface even for a PR, per
       GitHub's API). `roadmap-tracking-issues.yml` already declares the same permission for
       issue/label writes.

A PR can touch more than one item. This is rare but not forbidden. The workflow then reconciles
toward the union of every touched item's desired topic. A topic still held by any in-scope item
stays labeled, even when another item is reclassified away from it.

## Alternatives considered

- **`actions/labeler` (path-glob → label mapping).** The standard off-the-shelf action matches file
  *paths* against glob patterns. It cannot fit here, because the path does not encode the topic.
  Every item lives at the same shape, `roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md`, regardless of
  topic. The topic exists inside the file's metadata, which `actions/labeler` never reads.
  Rejected.
- **Label the PR from the merge-time `roadmap-id` workflow instead of at PR-open time.**
  [`roadmap-id.yml`](../../.github/workflows/roadmap-id.yml) runs on `push: main` *after* merge.
  Labeling there would arrive too late for the stated goal, a reviewer triaging which *open* PRs
  to look at. By then the PR is already closed. Rejected.
- **Ask the item's author to add or update the label by hand** (document the convention, rely on
  `make new-roadmap-item`'s prompt or a PR template checklist). Rejected as manual toil that will
  silently drift. A forgotten label, or one left stale after a `Topic` change, is invisible
  because no gate catches it. In the automated approach, code enforces the mapping every time.
- **Add labels and never remove one.** This is simpler, and it was the original scope of this item
  before the item grew to cover `Topic` edits. Rejected once relabeling was in scope. Leaving the
  old topic's label in place after a `Topic` change would leave the PR carrying two labels, one
  accurate and one stale. The stale label misleads the topic-based filtering this item exists to
  support.
- **Emit a base→head delta (`remove old`, `add new`) per changed item, without reading the PR's
  current labels.** This was the first design, and the one this proposal originally described.
  Rejected during implementation, once its per-push behavior was traced. `pull_request` fires on
  every `synchronize`, but a label set is stateful, and a base→head delta is not convergent. A
  *new* item keeps status `added` across pushes, so a delta only ever *adds*. Reclassifying the
  item mid-review (the marquee case in *Motivation*) leaves the PR carrying **both** the old and new
  topic label. Reverting a `Topic` edit drops the file from the diff, which strands the label an
  earlier push added. The fix is to make the operation declarative: recompute the desired set from
  the whole base→head diff each run and reconcile it against the PR's current `topic:*` labels
  (piece 2). This converges regardless of prior label state, at the cost of one extra read
  (`gh pr view --json labels`).
- **Also label items whose `Status` is `Implemented`.** Rejected; see *Motivation*. A shipped item
  has no open PR left to triage, so a label has nothing to route. (Before BE-0159 flattened the
  layout, this exclusion was a path check against `roadmaps/implemented/`. It is now the same
  decision read from the item's `Status`.)
- **Recompute the previous `Topic` with a full-history checkout (`fetch-depth: 0`) instead of a
  single `git show` against the base commit.** Both give the same answer. A full checkout is
  unnecessary cost when fetching the one base commit already needed is enough. Rejected as heavier
  for no benefit.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `scripts/sync_roadmap_topic_labels.py`: changed-file entries → add/remove `topic:<key>`
      actions, reusing `build_roadmap_index.py`'s metadata parsing and `TOPIC_KEY_BY_NAME`.
- [x] `.github/workflows/roadmap-topic-labels.yml`: detect added/modified/renamed item files
      in the flat roadmap tree (skipping `Implemented` items by `Status`), ensure each needed
      topic label exists, and apply the add/remove actions to the PR.

Log:

- Implemented both units in one change: the reconciling classifier
  (`scripts/sync_roadmap_topic_labels.py`, unit-tested in `tests/test_sync_roadmap_topic_labels.py`)
  and the `roadmap-topic-labels.yml` workflow that applies its actions to the PR. Pre-merge review
  found the originally-specified base→head delta was not convergent across pushes (it accumulated
  both labels when a new item was reclassified mid-review); reshaped piece 2 to reconcile a desired
  set against the PR's current `topic:*` labels instead (see *Alternatives considered*).
- Reworked onto the BE-0159 flat roadmap layout before merge: the item moved to
  `roadmaps/BE-0156-roadmap-topic-label-sync/`, the workflow's path filter widened to `roadmaps/**`,
  and the shipped-item exclusion moved from an `implemented/` path check to reading each item's
  `Status` (skipping `Implemented`) — since the per-`Status` folders no longer exist.
- [#817](https://github.com/bajutsu-e2e/bajutsu/pull/817): extended the path-label rules with a
  `record` topic — a PR touching the record modules (`bajutsu/record.py`, `bajutsu/record_capture.py`,
  `bajutsu/cli/commands/record.py`) now carries `topic:record`. Required promoting `record` to a
  canonical `TOPICS` key so the `PATH_TOPIC_*` guard accepts it; existing record items stay under
  `authoring`, so the index is unchanged.

## References

- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) — canonical
  `TOPICS` tuple and BE-METADATA parsing this item reuses.
- [`.github/workflows/roadmap-proposal-approvals.yml`](../../.github/workflows/roadmap-proposal-approvals.yml) —
  precedent for a roadmap-scoped PR workflow with the same no-op-when-out-of-scope shape.
- [`.github/workflows/roadmap-tracking-issues.yml`](../../.github/workflows/roadmap-tracking-issues.yml) —
  precedent (BE-0109) for the open/`Implemented` boundary this item reuses, and for reading
  metadata via the same parsing utilities.
- [`.github/workflows/roadmap-id.yml`](../../.github/workflows/roadmap-id.yml) — the
  merge-time workflow considered and rejected as the labeling point (see *Alternatives
  considered*).

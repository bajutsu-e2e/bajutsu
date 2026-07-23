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

Add a GitHub Actions workflow that keeps each open roadmap item's PR labeled with a
`topic:<key>` label matching its current `Topic` metadata field — automatically, with no
author or reviewer action required. It covers two cases: a PR that adds a brand new item
gets its topic label; a PR that changes the `Topic` on an already-numbered item (in any
status other than `Implemented`) gets relabeled to match.

## Motivation

The roadmap already groups every item under one of 23 topics
([`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py)'s `TOPICS`
tuple — e.g. "Backend expansion (iOS actuators)", "Integration & automation",
"Security hardening"), and the index pages render items grouped by topic. But that grouping
only becomes visible once someone opens the item file and reads its metadata block, or
waits for the next index regeneration. On the GitHub PR list itself — where reviewers
triage which items to look at — a PR carries no topic signal beyond its title.

A `topic:<key>` label surfaces that grouping immediately in the PR list and in
notifications, letting a reviewer who owns "Security hardening" or "codegen coverage"
filter to the PRs that concern them without opening each diff. It also gives a free,
queryable history: `is:pr label:topic:mcp` finds every PR ever raised for a topic, open or
closed.

A `Topic` is not fixed at proposal time — reclassifying an item (moving it from one topic
to a better-fitting one, splitting a catch-all topic once it grows) is a normal part of
shaping the roadmap, and it happens on already-numbered items at any `Status` short of
`Implemented`, not only brand-new proposals. If labeling only ever ran once at
PR-open time, a PR whose `Topic` changes mid-review would carry a stale label for the rest
of its life, actively misleading the same triage the label exists to help. So the
labeling has to track edits, not just additions.

`Implemented` items are out of scope for the relabel side: once an item has shipped, there
is no more open PR left to triage by topic, and further prose edits to a shipped item's
file are not the kind of routing decision this label exists to surface. This mirrors the
open/shipped boundary [`roadmap-tracking-issues.yml`](../../.github/workflows/roadmap-tracking-issues.yml)
(BE-0109) already draws: it keeps a tracking issue open for `Proposal` / `In progress`
items and closes it once an item ships, never touching `Implemented` items.

This is pure PR triage tooling — it never influences `run`, the deterministic gate, or any
pass/fail verdict (prime directive 1), and it touches no per-app behavior (prime
directive 3).

## Detailed design

The work is three independent pieces:

1. **Topic → label mapping, reusing the existing canonical topic list.** The 23 `(name,
   key, has_origin)` tuples in `TOPICS`
   ([`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py)) are
   already the single source of truth mapping a human-readable `Topic` value (as it
   appears in a BE item's metadata block) to a short, stable key (e.g. `"Backend expansion
   (iOS actuators)"` → `backend`). The label name is `topic:<key>` (e.g. `topic:backend`,
   `topic:mcp`, `topic:security`) — short enough to scan in the PR list's label row, and
   because it's the same key the index already uses, adding a 24th topic never needs a
   separate label-mapping update.

2. **A script that turns "how this PR changed roadmap items" into "labels to add / remove".**
   A new `scripts/sync_roadmap_topic_labels.py` is **reconciling, not a diff replay**. From the
   PR's changed-file entries (path, status, and — for a rename — the previous path), restricted
   to English item files (the non-`-ja` file per item directory) in the flat
   `roadmaps/BE-NNNN-<slug>/` tree (BE-0159 retired the per-`Status` folders), it first computes
   the PR's **desired** `topic:<key>` set. An item whose head `Status` is `Implemented` is skipped
   — a shipped item has no open PR left to triage; since there is no longer an `implemented/`
   folder to key on, this reads the item's `Status` from the same head content:
   - **Added** (a brand new item): read its `Topic` from the working tree (the PR head) → its
     label is desired.
   - **Modified or renamed** (an edit to an already-numbered item, including a slug rename):
     read the current `Topic` from the working tree, and the previous `Topic` from the base
     commit — `git show <base-sha>:<old-path>` (the old path is the same as the current one
     unless the entry is a rename) — via the same `<!-- BE-METADATA -->` parsing
     (`META_BLOCK_RE` / `META_ROW_RE`) already in `build_roadmap_index.py`, reused rather than
     reimplemented. If the two `Topic` values are equal (the common case — most edits don't
     touch `Topic`), the item contributes nothing, so a prose-only edit never labels a PR on its
     own; a `Status` change alone is likewise not a trigger. If they differ, the **new** topic's
     label is desired.
   - A `Topic` value absent from `TOPIC_KEY_BY_NAME` (an item authored by hand, bypassing
     `make new-roadmap-item`'s validation) is not an error: the script emits an `::warning::`
     line for that file and leaves it out of the desired set, so a labeling gap never blocks or
     fails the PR.

   It then **reconciles** that desired set against the PR's *current* `topic:*` labels (passed in
   by the workflow): it prints one `add <label>` line per desired-but-absent label and one
   `remove <label>` line per present-but-no-longer-desired label. Because GitHub's
   `pulls/{pr}/files` is the whole base→head diff, the desired set is a pure function of the
   current head and is recomputed in full on every push — so reconciliation **converges** where a
   naive "remove old, add new" delta would not (see *Alternatives considered*).

3. **A workflow, `.github/workflows/roadmap-topic-labels.yml`.** Triggers on `pull_request`
   (defaulting to `opened` / `reopened` / `synchronize`) path-filtered to the whole flat
   `roadmaps/**` tree — the script narrows to English item files and skips `Implemented` ones
   (see *Motivation*), so no folder-level path filter is needed. It skips fork PRs (whose token is
   read-only). Steps:
   - Check out the PR **head** (`ref: <head.sha>`), not the default `pull_request` merge ref: the
     changed-file list and the base comparison are both relative to the head, so the working tree
     the script reads each item's current `Topic` from must be the head, not head-merged-onto-base.
   - Fetch the PR's base commit
     (`git fetch --depth=1 origin ${{ github.event.pull_request.base.sha }}`) so `git show` can
     read each modified/renamed file's previous content. This is **non-fatal**: if the base SHA is
     unreachable (e.g. the base branch was force-pushed after the run queued), the job warns and
     exits green rather than red — pure triage tooling never gates a PR.
   - List the PR's changed files via `gh api pulls/{pr}/files` (`status`, `filename`,
     `previous_filename`), and read the PR's current labels via `gh pr view --json labels`; feed
     both to `scripts/sync_roadmap_topic_labels.py` to get the add/remove actions.
   - If it prints nothing (a PR touching only the generated index, or one already in sync), exit
     green as a no-op — the same discipline `roadmap-proposal-approvals.yml` and
     `roadmap-tracking-issues.yml` already follow for out-of-scope PRs.
   - For each *add* action's label, `gh label create <name> --color <fixed-color> --force`
     (idempotent — creates it on first use of a topic, updates harmlessly if it already exists) so
     a brand new topic never needs a manual label-creation step; every `topic:*` label shares one
     fixed color so they read as a family distinct from `bug` / `documentation` / etc. The
     add/remove labels are then applied in **two** `gh pr edit` calls (one `--add-label`, one
     `--remove-label`), not one round-trip per label; the remove is best-effort — a label already
     absent (e.g. a maintainer removed it by hand) is not an error.
   - Needed permissions: `contents: read` (checkout and `git show` against the base commit),
     `pull-requests: write` (list files, read/edit labels), `issues: write` (label creation lives
     under the Issues REST surface even for a PR, per GitHub's API — mirroring what
     `roadmap-tracking-issues.yml` already declares for issue/label writes).

A PR that touches more than one item (rare, but not forbidden) reconciles toward the union of
every touched item's desired topic, so a topic still held by any in-scope item stays labeled even
when another item is reclassified away from it.

## Alternatives considered

- **`actions/labeler` (path-glob → label mapping).** The standard off-the-shelf action
  matches file *paths* against glob patterns. It can't fit here because the topic isn't
  encoded in the path — every item lives at the same shape,
  `roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md`, regardless of topic. The topic
  only exists inside the file's metadata, which `actions/labeler` never reads. Rejected.
- **Label the PR from the merge-time `roadmap-id` workflow instead of at PR-open time.**
  [`roadmap-id.yml`](../../.github/workflows/roadmap-id.yml) runs on `push: main` *after*
  merge. Labeling there would arrive too late to help the stated goal — a reviewer
  triaging which *open* PRs to look at — since by then the PR is already closed. Rejected.
- **Ask the item's author to add or update the label by hand** (document the convention,
  rely on `make new-roadmap-item`'s prompt or a PR template checklist). Rejected as manual
  toil that will silently drift: a forgotten label, or one left stale after a `Topic`
  change, is invisible (no gate catches it), unlike the automated approach where the
  mapping is enforced by code every time.
- **Only ever add labels, never remove one.** Simpler, and was the original scope of this
  item before it grew to cover `Topic` edits. Rejected once relabeling was in scope: leaving
  the old topic's label in place after a `Topic` change would leave the PR carrying two
  labels — one accurate, one stale — actively misleading exactly the topic-based filtering
  this item exists to support.
- **Emit a base→head delta (`remove old`, `add new`) per changed item, without reading the PR's
  current labels.** The first design, and the one this proposal originally described. Rejected
  during implementation once its per-push behavior was traced: `pull_request` fires on every
  `synchronize`, but a label set is stateful, and a base→head delta is not convergent. A *new*
  item keeps status `added` across pushes, so a delta only ever *adds* — reclassifying it
  mid-review (the marquee case in *Motivation*) leaves the PR carrying **both** the old and new
  topic label. Reverting a `Topic` edit drops the file from the diff, stranding the label an
  earlier push added. The fix is to make the operation declarative: recompute the desired set from
  the whole base→head diff each run and reconcile it against the PR's current `topic:*` labels
  (piece 2). This converges regardless of prior label state, at the cost of one extra read
  (`gh pr view --json labels`).
- **Also label items whose `Status` is `Implemented`.** Rejected: see *Motivation* — a shipped
  item has no open PR left to triage, so there is nothing for a label to route. (Before BE-0159
  flattened the layout this exclusion was a path check against `roadmaps/implemented/`; it is now
  the same decision read from the item's `Status`.)
- **Recompute the previous `Topic` with a full-history checkout (`fetch-depth: 0`) instead
  of a single `git show` against the base commit.** Both give the same answer; a full
  checkout is unnecessary cost when fetching just the one base commit already needed is
  enough. Rejected as heavier for no benefit.

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

**English** · [日本語](BE-XXXX-roadmap-topic-label-sync-ja.md)

# BE-XXXX — Keep roadmap-item PR labels in sync with Topic

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-roadmap-topic-label-sync.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Development infrastructure (contributor workflow) |
<!-- /BE-METADATA -->

## Introduction

Add a GitHub Actions workflow that keeps each open roadmap item's PR labeled with a
`topic:<key>` label matching its current `Topic` metadata field — automatically, with no
author or reviewer action required. It covers two cases: a PR that adds a brand new item
gets its topic label; a PR that changes the `Topic` on an already-numbered item (in any
status other than `Implemented`) gets relabeled to match.

## Motivation

The roadmap already groups every item under one of 23 topics
([`scripts/build_roadmap_index.py`](../../../scripts/build_roadmap_index.py)'s `TOPICS`
tuple — e.g. "Backend expansion (iOS actuators)", "Integration & automation (MCP)",
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
shaping the roadmap, and it happens on already-numbered items in `roadmaps/in-progress/`
and `roadmaps/deferred/`, not only brand-new proposals. If labeling only ever ran once at
PR-open time, a PR whose `Topic` changes mid-review would carry a stale label for the rest
of its life, actively misleading the same triage the label exists to help. So the
labeling has to track edits, not just additions.

`Implemented` items are out of scope for the relabel side: once an item has shipped, there
is no more open PR left to triage by topic, and further prose edits to a shipped item's
file are not the kind of routing decision this label exists to surface. This mirrors the
open/shipped boundary [`roadmap-tracking-issues.yml`](../../../.github/workflows/roadmap-tracking-issues.yml)
(BE-0109) already draws: it keeps a tracking issue open for `Proposal` / `In progress`
items and closes it once an item ships, never touching `Implemented` items.

This is pure PR triage tooling — it never influences `run`, the deterministic gate, or any
pass/fail verdict (prime directive 1), and it touches no per-app behavior (prime
directive 3).

## Detailed design

The work is three independent pieces:

1. **Topic → label mapping, reusing the existing canonical topic list.** The 23 `(name,
   key, has_origin)` tuples in `TOPICS`
   ([`scripts/build_roadmap_index.py`](../../../scripts/build_roadmap_index.py)) are
   already the single source of truth mapping a human-readable `Topic` value (as it
   appears in a BE item's metadata block) to a short, stable key (e.g. `"Backend expansion
   (iOS actuators)"` → `backend`). The label name is `topic:<key>` (e.g. `topic:backend`,
   `topic:mcp`, `topic:security`) — short enough to scan in the PR list's label row, and
   because it's the same key the index already uses, adding a 24th topic never needs a
   separate label-mapping update.

2. **A script that turns "how this PR changed roadmap items" into "labels to add / remove".**
   A new `scripts/sync_roadmap_topic_labels.py` takes the PR's changed-file entries (path,
   status, and — for a rename — the previous path) restricted to item files (the
   non-`-ja` file per item directory) under `roadmaps/proposals/`, `roadmaps/in-progress/`,
   and `roadmaps/deferred/`, and classifies each:
   - **Added** (a brand new item): read its `Topic` from the working tree (the PR head) →
     emit an *add* action for that topic's label. No prior state to compare against.
   - **Modified or renamed** (an edit to an already-numbered item, including a slug rename):
     read the current `Topic` from the working tree, and the previous `Topic` from the base
     commit — `git show <base-sha>:<old-path>` (the old path is the same as the current one
     unless the entry is a rename) — via the same `<!-- BE-METADATA -->` parsing
     (`META_BLOCK_RE` / `META_ROW_RE`) already in `build_roadmap_index.py`, reused rather
     than reimplemented. If the two `Topic` values are equal (the common case — most edits
     don't touch `Topic`), no action. If they differ, emit a *remove* action for the old
     topic's label and an *add* action for the new one. A `Status` change alone (e.g.
     `Proposal` → `In progress`) is not itself a trigger — only a `Topic` value change is.
   - A `Topic` value absent from `TOPIC_KEY_BY_NAME` (an item authored by hand, bypassing
     `make new-roadmap-item`'s validation) is not an error: the script emits an
     `::warning::` line for that file and produces no action for it, so a labeling gap never
     blocks or fails the PR.
   The script prints one `add <label>` or `remove <label>` line per action (deduplicated) to
   stdout for the workflow to execute.

3. **A workflow, `.github/workflows/roadmap-topic-labels.yml`.** Triggers on
   `pull_request: [opened, reopened, synchronize]` path-filtered to `roadmaps/proposals/**`,
   `roadmaps/in-progress/**`, and `roadmaps/deferred/**` — deliberately not
   `roadmaps/implemented/**` (see *Motivation*). Steps:
   - Check out the PR head normally (default `actions/checkout` behavior gives the working
     tree needed to read each changed file's current `Topic`).
   - List the PR's changed files via `gh api pulls/{pr}/files` (`status`, `filename`,
     `previous_filename`), keeping only entries under the three included path prefixes that
     are item files.
   - If that set is empty, exit green as a no-op (a PR touching only the generated index, or
     one that doesn't touch an item file, has nothing to (re)label) — the same no-op
     discipline `roadmap-proposal-approvals.yml` and `roadmap-tracking-issues.yml` already
     follow for out-of-scope PRs.
   - Fetch the PR's base commit (`git fetch origin ${{ github.event.pull_request.base.sha }}`)
     so `git show` can read each modified/renamed file's previous content, then run
     `scripts/sync_roadmap_topic_labels.py` on the changed-file list to get the add/remove
     actions.
   - For each *add* action's label, `gh label create <name> --color <fixed-color> --force`
     (idempotent — creates it on first use of a topic, updates harmlessly if it already
     exists) so a brand new topic never needs a manual label-creation step; every `topic:*`
     label shares one fixed color so they read as a family distinct from `bug` /
     `documentation` / etc. Then `gh pr edit <pr> --add-label <name>`.
   - For each *remove* action's label, `gh pr edit <pr> --remove-label <name>`
     (best-effort — a label already absent from the PR, e.g. a maintainer removed it by
     hand, is not an error).
   - Needed permissions: `contents: read` (checkout and `git show` against the base commit),
     `pull-requests: write` (list files, edit labels), `issues: write` (label creation lives
     under the Issues REST surface even for a PR, per GitHub's API — mirroring what
     `roadmap-tracking-issues.yml` already declares for issue/label writes).

A PR that touches more than one item (rare, but not forbidden) gets the union of every
item's add/remove actions — one label change per distinct topic transition, not per item.

## Alternatives considered

- **`actions/labeler` (path-glob → label mapping).** The standard off-the-shelf action
  matches file *paths* against glob patterns. It can't fit here because the topic isn't
  encoded in the path — every item lives at the same shape,
  `roadmaps/<category>/BE-NNNN-<slug>/BE-NNNN-<slug>.md`, regardless of topic. The topic
  only exists inside the file's metadata, which `actions/labeler` never reads. Rejected.
- **Label the PR from the merge-time `roadmap-id` workflow instead of at PR-open time.**
  [`roadmap-id.yml`](../../../.github/workflows/roadmap-id.yml) runs on `push: main` *after*
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
- **Also relabel PRs touching `roadmaps/implemented/**`.** Rejected: see *Motivation* — an
  implemented item has no open PR left to triage, so there is nothing for a label to route.
- **Recompute the previous `Topic` with a full-history checkout (`fetch-depth: 0`) instead
  of a single `git show` against the base commit.** Both give the same answer; a full
  checkout is unnecessary cost when fetching just the one base commit already needed is
  enough. Rejected as heavier for no benefit.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `scripts/sync_roadmap_topic_labels.py`: changed-file entries → add/remove `topic:<key>`
      actions, reusing `build_roadmap_index.py`'s metadata parsing and `TOPIC_KEY_BY_NAME`.
- [ ] `.github/workflows/roadmap-topic-labels.yml`: detect added/modified/renamed item files
      under the open-status roadmap directories, ensure each needed topic label exists, and
      apply the add/remove actions to the PR.

## References

- [`scripts/build_roadmap_index.py`](../../../scripts/build_roadmap_index.py) — canonical
  `TOPICS` tuple and BE-METADATA parsing this item reuses.
- [`.github/workflows/roadmap-proposal-approvals.yml`](../../../.github/workflows/roadmap-proposal-approvals.yml) —
  precedent for a roadmap-scoped PR workflow with the same no-op-when-out-of-scope shape.
- [`.github/workflows/roadmap-tracking-issues.yml`](../../../.github/workflows/roadmap-tracking-issues.yml) —
  precedent (BE-0109) for the open/`Implemented` boundary this item reuses, and for reading
  metadata via the same parsing utilities.
- [`.github/workflows/roadmap-id.yml`](../../../.github/workflows/roadmap-id.yml) — the
  merge-time workflow considered and rejected as the labeling point (see *Alternatives
  considered*).

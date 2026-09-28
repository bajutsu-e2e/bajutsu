**English** · [日本語](BE-XXXX-roadmap-approved-status-rename-ja.md)

# BE-XXXX — Rename the roadmap Status value Proposal to Approved

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-roadmap-approved-status-rename.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#2075](https://github.com/bajutsu-e2e/bajutsu/pull/2075) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

A roadmap item's `Status` field takes one of five values today
([BE-0366](../BE-0366-roadmap-rejected-status/BE-0366-roadmap-rejected-status.md)):
`Implemented`, `In progress`, `Proposal`, `Deferred`, and `Rejected`. This item renames one of
them: `Proposal` (Japanese: 提案) becomes `Approved` (Japanese: 承認済み). The other four values,
and their Japanese labels, stay unchanged. The rename touches every place that reads or writes the
literal `Status` value — roadmap scripts, CI workflows, existing roadmap item files, documentation,
Agent Package Manager (APM) skill sources, and their gate tests — and nothing else. It leaves the
generic word "proposal", which names the act of proposing a new roadmap item rather than a `Status`
value, exactly where it stands today: the `ideation` and `propose-and-build` skill names, the
`roadmap-proposal-approvals.yml` workflow's job name and its exemption label
`single-approver proposal`, and the roadmap metadata table's separate `Proposal` field, which links
to the item's own file (a self-reference, in the Swift-Evolution style every item's metadata block
already follows). It also leaves the dashboard bucket's indigo color and
the `class Proposal` dataclass in `bajutsu/common/agents/protocols/proposal.py` — an unrelated
data type an agent uses to propose its next action — untouched.

## Motivation

A roadmap item whose `Status` is `Proposal` needs two reviewer approvals before it merges to
`main`. The [`roadmap-proposal-approvals.yml`](../../.github/workflows/roadmap-proposal-approvals.yml)
workflow enforces this by comparing the item's `Status` field against the literal string
`"Proposal"` ([L88](../../.github/workflows/roadmap-proposal-approvals.yml#L88)). A pull request
carrying the exemption label `single-approver proposal` needs only one approval instead
([L101-110](../../.github/workflows/roadmap-proposal-approvals.yml#L101-L110)).

Most items that reach `main` with `Status: Proposal`, therefore, already cleared a two-reviewer
review — the exemption label covers only a minority of merges. The name `Proposal` nonetheless
reads as "still under consideration, not yet decided". A contributor scanning the roadmap dashboard
can misread a reviewed, ready-to-build item as one the maintainers have not yet accepted. A new
contributor who picks a first task from the dashboard's "Show open only" view acts on that
misreading directly, passing over ready-to-build work as if it still awaited a decision.

Renaming the value to `Approved` removes the mismatch: a status a reader sees only after the
review gate has passed — two approvals, or one under the `single-approver proposal` waiver — reads
as passed. After this item ships, `make roadmap-status
STATUS="Proposal"` returns no items, `make roadmap-status STATUS="Approved"` returns the items that
used to carry `Proposal`, and the roadmap dashboard's bucket that used to read "Proposals" reads
"Approved".

The rename does not conflict with the "the code decides the Status" principle
[`docs/ai-development.md`](../../docs/ai-development.md#roadmap-items-be-ids-strict) states: that
principle's axis is whether an item's implementation exists, not whether the item has been
reviewed. `Approved` is a new name for the same status — no implementation yet — not a new axis. A
freshly scaffolded item, before its proposal PR has seen any review, also carries `Status: Approved`
under this rule, and `roadmap-filter` ([`scripts/roadmap_query.py`](../../scripts/roadmap_query.py))
reads a `BE-XXXX` placeholder on a local branch exactly like a numbered item, so a session working on
an unreviewed proposal can see that value before review. That is not a new failure mode: the
scaffolder's previous default, `Proposal`, was visible the same way on a local branch before this
item shipped, and `Status` has never claimed to describe anything beyond "does this item's
implementation exist" — a freshly scaffolded item, whatever its `Status` reads, plainly has none.
The misreading this item removes is the public one: a stranger reading the **published** dashboard
or running `roadmap-filter` against `main`, where every listed item already cleared the review gate
— two approvals, or one under the waiver label — before the value could appear there at all. The
paragraph in `docs/ai-development.md` that
states the principle itself names `Proposal`, so it is in scope for this item's documentation
updates too, alongside a few surfaces that call implementing a `Proposal` the moment it is
*accepted* ([`.apm/skills/implement-be/SKILL.md`](../../.apm/skills/implement-be/SKILL.md),
[`docs/roadmap-workflow.md`](../../docs/roadmap-workflow.md),
[`docs/contributor-workflow-tutorial.md`](../../docs/contributor-workflow-tutorial.md)): this item's
own premise is that acceptance already happened at the two-approval merge, so those surfaces are
reworded to say implementing an `Approved` item **starts** it, not that starting it accepts it
(see *Detailed design*).

## Detailed design

### Scripts that read or write the literal value

| File | Change |
|---|---|
| [`scripts/check_roadmap_format.py`](../../scripts/check_roadmap_format.py) | Rename the `STATUS_PAIR` entry `"Proposal": "提案"` to `"Approved": "承認済み"`. `ORDER_EN` / `REQUIRED_EN` / `REQUIRED_JA` name the metadata table's separate `Proposal` / `提案` field and stay as they are. |
| [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) | Rename the `STATUS_TO_BUCKET` entry `"Proposal": "Proposals"` to `"Approved": "Approved"`, and the `BUCKETS` tuple's `("Proposals", "proposals")` to `("Approved", "approved")`. Update the module docstring and the `STATUS_TO_BUCKET` comment, both of which name the `Proposals` bucket. |
| [`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py) | Rename the `BUCKET_COLOR` key and the `BUCKET_LABEL` entry `"Proposals": "Proposal"` to `"Approved"` (key and badge value both), keeping the indigo swatch `#534AB7`. Update the stale "indigo as proposed" comment, the `OPEN_BUCKETS=['Proposals', 'In progress']` list, the module docstring (which doubles as the command-line interface (CLI) help text), and the two sentences in the page's own published introduction (`_INTRO`) that name `Proposal`. |
| [`scripts/sync_roadmap_tracking_issues.py`](../../scripts/sync_roadmap_tracking_issues.py) | Rename `OPEN_STATUSES = frozenset({"Proposal", "In progress"})` to `frozenset({"Approved", "In progress"})`, and its docstring and comments naming `Proposal`. |
| [`scripts/new_roadmap_item.py`](../../scripts/new_roadmap_item.py) | Rename the `--status` default from `"Proposal"` to `"Approved"`, its `--status` help string, and the docstring's `[STATUS=Proposal]` example. |
| [`scripts/roadmap_query.py`](../../scripts/roadmap_query.py) | No literal to rename: `VALID_STATUSES` derives from `STATUS_TO_BUCKET`, so it follows the index change automatically. Update the two docstring usage examples that name `Proposal`. |
| [`scripts/sync_roadmap_topic_labels.py`](../../scripts/sync_roadmap_topic_labels.py) | Update the comment next to `SHIPPED_STATUS = "Implemented"` that lists `Proposal` among the other statuses; the value itself stays `"Implemented"`. |

### CI workflows

[`roadmap-proposal-approvals.yml`](../../.github/workflows/roadmap-proposal-approvals.yml) keeps
its job name, `require two approvals for BE proposals`, and its exemption label,
`single-approver proposal`, since both name the act of proposing a new item rather than a `Status`
value. It renames only the literal comparison at
[L88](../../.github/workflows/roadmap-proposal-approvals.yml#L88) — `if [ "$status" = "Proposal" ]`
becomes `"Approved"` — the `::notice::` text at
[L98](../../.github/workflows/roadmap-proposal-approvals.yml#L98), and the `Status` value mentioned
in comments at [L12](../../.github/workflows/roadmap-proposal-approvals.yml#L12) and
[L59-60](../../.github/workflows/roadmap-proposal-approvals.yml#L59-L60).
[`roadmap-tracking-issues.yml`](../../.github/workflows/roadmap-tracking-issues.yml)'s comment
naming `Status Proposal / In progress` at
[L4](../../.github/workflows/roadmap-tracking-issues.yml#L4), and the `Status` transition described
in [`.github/roadmap-refresh-prompt.md`](../../.github/roadmap-refresh-prompt.md), rename the same
way.

### Migrating existing roadmap items

Every roadmap item file rewrites its `Status` / `状態` row inside its **leading**
`<!-- BE-METADATA -->` … `<!-- /BE-METADATA -->` block only — the first fenced pair in the file, not
every fenced pair a whole-file scan would find. That distinction matters for
[BE-0074](../BE-0074-be-template-standardization/BE-0074-be-template-standardization.md): its own
`Status` is `Implemented`, but its body's canonical-skeleton code block quotes a *second*
`<!-- BE-METADATA -->` pair as a template example, including `| Status | **Proposal** |`. Scoping
only to "inside a fence" would still catch that second pair; scoping to the *first* fence in the
file does not, so BE-0074's example line stays exactly as written — it illustrates the template as
it stood, not the item's current status. This differs from
[`roadmap-proposal-approvals.yml:86-87`](../../.github/workflows/roadmap-proposal-approvals.yml#L86-L87),
whose `sed` reads every fenced pair in a changed file: that is safe for a CI gate, which only ever
sees a PR that touches one item's own two files, but not for a repository-wide migration that also
touches BE-0074. On the Japanese side, the metadata block also carries a
`| 提案 | ... |` row linking to the item's own file — a field name that happens to share the string
`提案` with the old `Status` value. Only a full-line match of `| 状態 | **提案** |`
inside the metadata fence is replaced with `| 状態 | **承認済み** |`, so that field-name row is
left untouched.

### Documentation

The following pages enumerate `Status`'s possible values and are updated to list `Approved` in
place of `Proposal`: [`CLAUDE.md`](../../CLAUDE.md), [`roadmaps/README.md`](../../roadmaps/README.md)
and its [`README-ja.md`](../../roadmaps/README-ja.md) mirror,
[`docs/ai-development.md`](../../docs/ai-development.md) and its
[`docs/ja/ai-development.md`](../../docs/ja/ai-development.md) mirror,
[`docs/roadmap-workflow.md`](../../docs/roadmap-workflow.md) and its
[`docs/ja/roadmap-workflow.md`](../../docs/ja/roadmap-workflow.md) mirror — body prose, a diagram
alt text, and a mermaid diagram node; only the node change needs `make docs-diagrams` afterward, to
regenerate `docs/assets/diagrams/roadmap-workflow-cycle.svg` and
`docs/ja/assets/diagrams/roadmap-workflow-cycle-ja.svg` from the edited fences —
[`docs/contributor-workflow-tutorial.md`](../../docs/contributor-workflow-tutorial.md) and its
Japanese mirror, [`docs/overview.md`](../../docs/overview.md) and its Japanese mirror,
[`docs/specs/roadmap-dashboard-pagination-and-quick-filters.md`](../../docs/specs/roadmap-dashboard-pagination-and-quick-filters.md),
and the [`Makefile`](../../Makefile) help text for `new-roadmap-item` and `roadmap-status`.
`docs/architecture.md` and `docs/developer-guide.md`, [`docs/recording.md`](../../docs/recording.md),
and their Japanese mirrors (including the `docs/ja/developer-guide.md` class diagram) mention a
`Proposal` that names the unrelated agent dataclass from *Introduction* and stay as they are.

`docs/ai-development.md` and its Japanese mirror carry two wording fixes beyond the plain rename,
both following from this item's own premise that a two-approval merge, not the start of
implementation, is what accepts a proposal. The Status→bucket table's `Proposal` row reads
"Proposals — under consideration"; renamed literally it would read "Approved — under consideration",
contradicting itself, so it becomes "Approved — reviewed, not yet started". The same table's
`In progress` row reads "accepted, actively being built"; since acceptance now happens a row
earlier, it drops the now-redundant "accepted," and reads "actively being built". The same fix
carries into every other surface that calls the *start of implementation* the acceptance moment:
[`.apm/skills/implement-be/SKILL.md`](../../.apm/skills/implement-be/SKILL.md) (whose step 1 says
"implementing it *accepts* it"), `docs/roadmap-workflow.md` and its Japanese mirror, and
`docs/contributor-workflow-tutorial.md` and its Japanese mirror. Each becomes a variant of
"implementing an `Approved` item **starts** it" — the acceptance already happened when the item's
`Status` became `Approved`.

Dozens of already-`Implemented` roadmap items also name `Proposal` in their own body prose — a
design discussion, a precedent cited by a later item, a worked example — written when `Proposal`
was still the live value. Rewriting every one of them would spend this item's whole scope
relitigating history it did not set out to touch. This item follows the same line
[BE-0366](../BE-0366-roadmap-rejected-status/BE-0366-roadmap-rejected-status.md) already drew for
the same kind of rename: leave a historical account as the period-accurate record it is (the same
treatment BE-0074's template example already gets above), and rename the literal only where an
already-shipped item's prose describes a **still-operating mechanism** in the present tense, so
that prose would otherwise go stale the moment this item merges. Two items fit that test —
[BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) (the
tracking-issue lifecycle: "an open item... is one whose `Status` is `Proposal`...") and
[BE-0162](../BE-0162-roadmap-status-filter-skill/BE-0162-roadmap-status-filter-skill.md) (the
`roadmap-filter` skill's own valid `Status` values) — the same two items BE-0366 renamed for
identical reasons. Both (English and Japanese) rename `Proposal` to `Approved` in this same PR.

Two more items —
[BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
and
[BE-0216](../BE-0216-propose-and-build-parallel-skill/BE-0216-propose-and-build-parallel-skill.md)
— quote a literal, copy-pasteable command (`make new-roadmap-item …
[STATUS=Proposal]`, `Status: Proposal`) that this rename makes not merely stale but **actively
wrong**: `STATUS=Proposal` is no longer a value `check_roadmap_format.py` accepts, so following
either example today produces a file the format gate rejects. That is a stronger failure mode than
the narrative drift the still-operating-mechanism test targets, and it is worth fixing even in an
otherwise-historical item, so both (English and Japanese) are renamed too. Other candidates that
merely *narrate* the old vocabulary — e.g.
[BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md) and
[BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders.md),
which both list the dashboard's lifecycle buckets as "Implemented / In progress / Proposals /
Deferred" — are left alone: that four-item list has already read stale since
[BE-0366](../BE-0366-roadmap-rejected-status/BE-0366-roadmap-rejected-status.md) added a fifth
bucket, `Rejected`, without updating either mention, so neither item has been kept in step with the
live vocabulary regardless of this rename, and renaming only the one token their text happens to
share with this change would repair a coincidence, not the actual drift.
[BE-0100](../BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template.md)
and
[BE-0139](../BE-0139-roadmap-dashboard-issue-links/BE-0139-roadmap-dashboard-issue-links.md)
are the same kind of narrative account, not a runnable example, so they stay historical too. The
repository-wide grep in this item's last `Progress` box is expected to still find `Proposal` in:
the metadata field name, the `class Proposal` agent dataclass and its docs mentions, BE-0074's
template example, and this kind of historical prose in every other already-shipped item — a residual
match against any of those four is not a finding.

### APM skill sources

Six [`.apm/skills/`](../../.apm/skills/) skill sources name `Proposal` as a `Status` value:
`ideation`, `propose-and-build`, `roadmap-filter`, `task-select`, `implement-be`, and
`be-progress-tracker` (whose `Proposal (pre-allocation)` phrasing becomes
`Approved (pre-allocation)`). `ideation`'s own mention of the metadata table's `Proposal` field name
stays as it is, for the same reason the metadata table itself does. `implement-be` also carries the
"implementing it accepts it" wording fixed under *Documentation* above, since that sentence sits in
its own step 1. After editing the sources, `make skills` regenerates the deployed
[`.claude/skills/`](../../.claude/skills/) tree and [`apm.lock.yaml`](../../apm.lock.yaml) in the
same change; neither is hand-edited.

### Test code

Ten test files assert against the literal `"Proposal"` value: `tests/conftest.py`,
`tests/test_check_roadmap_format.py`,
`tests/test_fix_roadmap_drift.py`, `tests/test_lint_roadmap.py`, `tests/test_new_roadmap_item.py`,
`tests/test_roadmap_dashboard.py`, `tests/test_roadmap_index.py`, `tests/test_roadmap_query.py`,
`tests/test_sync_roadmap_topic_labels.py`, and `tests/test_sync_roadmap_tracking_issues.py`. Each
fixture, parametrization, and assertion updates to `"Approved"` (and, in the three files that also
build a Japanese fixture — `test_lint_roadmap.py`, `test_fix_roadmap_drift.py`,
`test_new_roadmap_item.py` — the Japanese value `提案` to `承認済み`). No test function name
encodes the old value, so none is renamed.
`tests/test_allocate_roadmap_ids.py` also names `Proposal`, but only as the metadata field name
(`* Proposal: [{be_id}]({name}.md)`) its fixture builds — the same field that stays as it is
everywhere else, so this file is not part of the rename.

### Prime-directive compliance

The whole surface is a metadata vocabulary, seven scripts, two CI workflows, roadmap item files,
documentation, six APM skill sources, and their gate tests. No large language model (LLM) enters
any path; `run` and CI
stay deterministic; nothing app-specific moves into the tool or its drivers.

## Alternatives considered

**Rename the generic word "proposal" as well** — extend the change to the `ideation` and
`propose-and-build` skill names and the `roadmap-proposal-approvals.yml` job name and exemption
label, unifying every use of the word under an "approval" vocabulary. Rejected: the act of proposing
a new roadmap item is a valid concept distinct from the `Status` value, and renaming the CI job would
change the required-check name a repository's branch protection ruleset references, which needs a
manual reset by a repository administrator. Neither cost buys anything toward this item's actual
goal — closing the gap between the `Status` name and what a two-approval merge already means.

**Use the shorter Japanese label 承認 instead of 承認済み.** Rejected: 承認 alone can also mean the
act of approving a pull request review, which risks confusion with that unrelated action. 承認済み
names a state the item is in — the approval has already happened — the way 実装済み does, rather
than the act of approving it.

**Accept the old value `Proposal` as a deprecated alias, migrating gradually.** Rejected: roadmap
item data is self-contained within this repository, with no external consumer depending on the
literal string `Status: Proposal`. The migration is a one-time, mechanical rewrite of the existing
roadmap item files, so there is no reason to keep an alias around.
[BE-0366](../BE-0366-roadmap-rejected-status/BE-0366-roadmap-rejected-status.md), the same kind of
`Status` enum rename (`Proposal (deferred)` → `Deferred`), also replaced its old value outright,
without keeping an alias.

**Scaffold a new item with a distinct pre-review value, and have the merge-time allocator
([BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)) flip it
to `Approved` when it numbers the item on `main`.** This would keep `Approved` from ever appearing
on an unmerged branch, at the cost of a sixth `Status` value that exists only between scaffolding
and merge, and a second thing (besides the id) for the allocator to rewrite — widening a job whose
whole point is a narrow, auditable, `roadmaps/`-only bypass push (`scripts/check_renumber_diff.py`
already caps its blast radius to that tree). Rejected: the value it would prevent from leaking,
`Approved` on an unreviewed branch, is not a new failure this item introduces — the previous
default, `Proposal`, was exactly as visible pre-merge, for the same reason (`Status` tracks
implementation existence, not review state; see *Motivation*). Solving a pre-existing non-problem
by growing the allocator's write surface is a worse trade than leaving it alone.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Rename `STATUS_PAIR`'s `"Proposal"` entry to `"Approved"` in `check_roadmap_format.py`.
- [x] Rename the `STATUS_TO_BUCKET` key and the `BUCKETS` entry in `build_roadmap_index.py`.
- [x] Rename the `BUCKET_COLOR` / `BUCKET_LABEL` / `OPEN_BUCKETS` entries and related comments in
      `build_roadmap_dashboard.py`.
- [x] Rename `OPEN_STATUSES` and its docstring in `sync_roadmap_tracking_issues.py`.
- [x] Rename the default `--status` value and its docstring example in `new_roadmap_item.py`.
- [x] Update the docstring examples in `roadmap_query.py` and the comment in
      `sync_roadmap_topic_labels.py`.
- [x] Rename the literal comparison and comments in `roadmap-proposal-approvals.yml` and
      `roadmap-tracking-issues.yml`, keeping the job name and exemption label unchanged.
- [x] Migrate every existing roadmap item's `<!-- BE-METADATA -->` `Status` / `状態` row (excluding
      BE-0074's body example) to `Approved` / `承認済み`.
- [x] Update the documentation pages listed in *Detailed design*, including the regenerated
      `docs/roadmap-workflow.md` diagrams, and rename the literal in BE-0109's and BE-0162's own
      body prose (English and Japanese), the two shipped items whose text describes a
      still-operating mechanism, plus BE-0069's and BE-0216's literal `make new-roadmap-item` /
      `Status:` examples, which the rename would otherwise leave actively broken rather than
      merely stale.
- [x] Rename the literal in the six APM skill sources and run `make skills`.
- [x] Rename the literal across the ten test files, including any test name that encodes it.
- [x] Verify with `make check` and a repository-wide grep for the retired literal.

Log:

- [#2075](https://github.com/bajutsu-e2e/bajutsu/pull/2075) landed all twelve units in one PR.
  `make roadmap-status STATUS="Proposal"` now fails with
  "unknown status"; `make roadmap-status STATUS="Approved"` returns the 18 migrated items — not
  this item itself, which ships as `Implemented`. A cold two-round self-review (BE-0347) surfaced
  two corrections beyond the plan this item's own text now reflects: the review-gate wording
  accounts for the `single-approver proposal` waiver rather than assuming every merge cleared two
  full approvals, and a freshly scaffolded item's `Approved` default is explained as harmless
  rather than claimed invisible pre-review (`roadmap-filter` reads a local `BE-XXXX` placeholder
  exactly like a numbered item; see *Motivation* and the rejected pre-review-value alternative).
  The review also caught that `BE-0109`, `BE-0162`, `BE-0069`, and `BE-0216` name the retired
  literal in text this rename would otherwise leave stale or actively broken, so all four were
  renamed in this same PR under the BE-0366 precedent and the "actively broken example" test —
  every other already-`Implemented` item's mention of `Proposal` is left as period-accurate
  history. A follow-up self-review against the live PR (Copilot, and the repository's own
  automated review) caught a workflow migration gap — the two-approval gate needed to accept the
  retired literal too, for any proposal PR still open on the pre-rename `main` — and several more
  wording slips this log now reflects.

## References

- [BE-0366 — Add a Rejected roadmap status, distinct from Deferred](../BE-0366-roadmap-rejected-status/BE-0366-roadmap-rejected-status.md)
  — the same kind of `Status` enum rename, cited under *Alternatives considered* for its precedent
  of replacing an old value outright rather than keeping an alias.
- [BE-0078 — Status-driven roadmap folders](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md)
  — introduced the `Status`-to-bucket vocabulary one of whose values this item renames.
- [BE-0089 — Merge-time BE-ID allocation on main](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)
  — merge-time id allocation is why a freshly scaffolded item reaches review still carrying the
  literal `BE-XXXX` placeholder, with whatever `Status` the scaffolder set — `Approved` by default,
  once this item ships (see *Motivation*).
- [`docs/ai-development.md`](../../docs/ai-development.md#roadmap-items-be-ids-strict) — the
  roadmap metadata rules and the "the code decides the Status" principle this item's *Motivation*
  addresses.
- [`roadmaps/README.md`](../../roadmaps/README.md) — the status list this item updates.

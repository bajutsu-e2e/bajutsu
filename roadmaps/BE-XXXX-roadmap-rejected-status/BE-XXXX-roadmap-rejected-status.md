**English** · [日本語](BE-XXXX-roadmap-rejected-status-ja.md)

# BE-XXXX — Add a Rejected roadmap status, distinct from Deferred

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-roadmap-rejected-status.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

A roadmap item's `Status` field takes one of four values today — `Implemented`, `In progress`,
`Proposal`, `Proposal (deferred)` — and that value alone decides its dashboard bucket
([BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md)). The fourth value
collapses two different situations into one bucket: a proposal that is merely parked, waiting on a
named blocker or a concrete future need, reads identically to a proposal that another BE item has
already made moot. This item splits that bucket in two. `Proposal (deferred)` becomes the bare
`Deferred`, matching the flat naming the other three values already use; a new `Rejected` value marks
a proposal the maintainers have decided against for good, whether because another BE item already
covers what it proposed or because review found no path that respects the project's prime directives
or scope. The change touches the metadata vocabulary, the dashboard generator, five roadmap scripts,
and their gate tests. It renames no item's directory, since `Status` has been directory-independent
since [BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders.md),
and it adds no LLM to any path.

## Motivation

Six roadmap items carry `Proposal (deferred)` today:
[BE-0027](../BE-0027-mock-server-external/BE-0027-mock-server-external.md),
[BE-0040](../BE-0040-ai-assertions/BE-0040-ai-assertions.md),
[BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split.md),
[BE-0154](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha.md),
[BE-0157](../BE-0157-shake-device-primitive/BE-0157-shake-device-primitive.md), and
[BE-0158](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive.md). Five of them
are genuinely parked: each names a concrete blocker or a future need that would revive it, from a
missing headless actuator for the shake and timezone primitives (BE-0157, BE-0158) to a design that
would let an AI-authored assertion lower to a deterministic check before `run` ever sees it (BE-0040).
The sixth, BE-0154 (running `roadmap-promote` from a base SHA), already carries a filled
`Superseded by` field naming BE-0159 as its successor: BE-0159 flattened the per-status folders
BE-0154's entire premise depended on, so no base SHA is left to promote from. Nothing about BE-0154 is
waiting on a blocker; its proposal is dead. Yet its `Status` reads the same `Proposal (deferred)` as
the five items that remain open questions, so a reader scanning the Deferred bucket on the dashboard
cannot tell which of the six are worth a second look without opening every file.

This conflation is one level below a problem BE-0078 already solved once. Before BE-0078, a live
proposal and one already accepted for active work shared a single `proposals/` folder,
indistinguishable without opening each file; BE-0078 split that bucket by giving `In progress` its own
value. The same argument now applies to a `Deferred` bucket that quietly mixes "still worth
revisiting" with "already answered by another BE".

The distinction is not limited to a previously deferred item that a later BE goes on to invalidate. A
live `Proposal`, or even an `In progress` item, can turn out not to be worth pursuing for reasons that
have nothing to do with a successor BE: review can find that the only literal implementation puts a
non-deterministic judgment on the `run`/CI gate, the tension BE-0040 comes close to before its own
text carves out an authoring-side design that stays inside the boundary, or the maintainers can simply
decide, after weighing it, that the idea does not belong. Each of these wants the same signal a
browsing reader needs: this proposal will not be pursued, and no named condition is expected to change
that. `Rejected` covers every one of them, not only the superseded case that surfaced the gap.

## Detailed design

### The five-value vocabulary

| Status (EN) | Status (JA) | Dashboard bucket |
|---|---|---|
| `Implemented` | `実装済み` | Implemented |
| `In progress` | `実装中` | In progress |
| `Proposal` | `提案` | Proposals |
| `Deferred` | `保留` | Deferred |
| `Rejected` | `却下` | Rejected |

`Deferred` renames `Proposal (deferred)` / `提案（保留）`; `Rejected` / `却下` is new. Dropping the
`Proposal (…)` wrapper from `Deferred` matches the naming BE-0078 already applied when it flattened
`Accepted, in progress` to `In progress`: the status word, the dashboard heading, and the badge text
should all read alike, and a value that is no longer a live proposal should not keep reading as one.

### Deferred vs. Rejected: the dividing line

`Deferred` keeps an item as a live question. Its own text names a concrete condition that would
revive it: a capability that does not exist yet (BE-0157, BE-0158: "no reliable, deterministic
actuator"), or a concrete future need the item spells out (BE-0027: "a backend whose behavior is too
stateful or protocol-heavy to express as declarative stubs"). The item is not being worked on, but the
question it raises stays open.

`Rejected` marks a proposal the maintainers have decided against, with no named condition expected to
reopen it. Two triggers land an item here: another BE item already covers what it proposed, in which
case the existing `Superseded by` field names the successor (the same reciprocal `Related` /
`Superseded by` convention [BE-0100](../BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template.md)
already defines, and that a shipped item's later replacement already uses — precedent:
[BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction.md),
[BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring.md));
or review found no path that respects the project's prime directives or scope, with the reasoning
recorded in the item's own `Alternatives considered` section. `Rejected` applies to a `Proposal` or an
`In progress` item as readily as to a previously `Deferred` one. It never applies to `Implemented`: an
item whose code shipped and mattered at the time keeps `Status: Implemented` even after a later BE
replaces it (the same BE-0125 / BE-0005 precedent), since rejecting it after the fact would misstate
what happened.

### Schema and generator changes

- [`scripts/check_roadmap_format.py`](../../scripts/check_roadmap_format.py) — `STATUS_PAIR`: rename
  the `"Proposal (deferred)": "提案（保留）"` entry to `"Deferred": "保留"`; add
  `"Rejected": "却下"`.
- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) — `STATUS_TO_BUCKET`:
  rename the `"Proposal (deferred)"` key to `"Deferred"` (the bucket name `"Deferred"` is unchanged);
  add `"Rejected": "Rejected"`. `BUCKETS`: append `("Rejected", "rejected")` after
  `("Deferred", "deferred")`, so the dashboard's most-progressed-first order ends `Implemented → In
  progress → Proposals → Deferred → Rejected`.
- [`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py) — `BUCKET_COLOR`:
  add a `"Rejected"` entry distinct from the existing grey `Deferred` swatch (`#5F5E5A`); a muted red
  such as `#8B3A3A` reads as "closed" the way the existing green / amber / indigo / grey set reads as
  "shipped / in flight / proposed / parked" (the final swatch and its contrast check belong to the
  implementing PR, not this proposal). `BUCKET_LABEL`: add `"Rejected": "Rejected"`. Update the module
  docstring's bucket list.
- [`scripts/new_roadmap_item.py`](../../scripts/new_roadmap_item.py) — `STATUS_JA`: rename the
  `"Proposal (deferred)"` key to `"Deferred"`; add `"Rejected": "却下"`, so
  `make new-roadmap-item STATUS=…` keeps accepting every value `check_roadmap_format.py` recognizes.
- [`scripts/sync_roadmap_tracking_issues.py`](../../scripts/sync_roadmap_tracking_issues.py) — no
  logic change: `OPEN_STATUSES = frozenset({"Proposal", "In progress"})` already treats anything else,
  `Rejected` included, as not open, and closes its tracking issue exactly as it already does for
  `Deferred`. Its docstring and inline comments name `Proposal (deferred)` as the shelved case; that
  wording is updated to name both `Deferred` and `Rejected` so the comment keeps matching the code.
- [`docs/ai-development.md`](../../docs/ai-development.md) and its
  [`docs/ja/ai-development.md`](../../docs/ja/ai-development.md) mirror — the Status→bucket table
  gains the `Rejected` row, and the `Deferred` row loses its `Proposal (…)` wrapper; the surrounding
  prose that names `Proposal (deferred)` follows.
- [`CLAUDE.md`](../../CLAUDE.md) — the status list `Status` (`Implemented` / `In progress` /
  `Proposal` / `Proposal (deferred)`) becomes five values, `Deferred` and `Rejected` both bare.
- [`roadmaps/README.md`](../README.md) and [`README-ja.md`](../README-ja.md) — the one-line status
  list in the "Adding a roadmap item" note gains `Rejected`.
- Gate tests — `tests/test_roadmap_format.py`, `tests/test_roadmap_index.py`,
  `tests/test_roadmap_query.py`, `tests/test_roadmap_dashboard.py`, `tests/test_new_roadmap_item.py`,
  and `tests/test_sync_roadmap_tracking_issues.py` each fix their status fixtures and assertions to
  the renamed `Deferred` and the new `Rejected` value.

### Migrating today's roadmap

The schema change above is inert until the six currently `Proposal (deferred)` items are reclassified
under it, in the same implementing PR:

- BE-0154 moves to `Rejected`. Its `Superseded by` field already names BE-0159 as the successor;
  nothing else needs to change beyond the `Status` value itself.
- BE-0157, BE-0158, BE-0070, and BE-0040 keep `Deferred`. Each names the concrete blocker or need that
  would revive it, so none of them fits `Rejected`'s "no named condition would reopen this" test.
- BE-0027 is the one genuinely mixed case: its own Introduction says in-protocol `mocks` have
  superseded it, but it also names a concrete revival condition (a stateful or protocol-heavy backend
  that outgrows the in-protocol stub language) and carries no filled `Superseded by` field. This item
  recommends leaving BE-0027 `Deferred` by default and flagging it for a maintainer's judgment call at
  implementation time, rather than letting the schema change silently decide a borderline case.

### Relationship to "Not adopting"

[`roadmaps/README.md`](../README.md)'s "Not adopting" section is a different mechanism and stays as it
is: it records an idea disqualified before it ever became a numbered BE item, under the roadmap's own
promotion rule, which starts an unformed idea under "Unsorted ideas" and promotes it to a numbered
item only once its scope is clear. `Rejected` applies only to an item that already exists under
`roadmaps/BE-NNNN-<slug>/`. This item does not propose retroactively promoting a "Not adopting" bullet
into a numbered item just to reject it, since that would spend a permanent BE id recording something
the bullet already records once.

### Prime-directive compliance

The whole surface is a metadata vocabulary, five scripts, and their gate tests. No LLM enters any
path; `run` and CI stay deterministic; nothing app-specific moves into the tool or its drivers.

## Alternatives considered

**Keep a single `Proposal (deferred)` value, and record "already dead" only through the existing
`Superseded by` field.** Rejected: dashboard bucketing and the `roadmap-filter` skill
([BE-0162](../BE-0162-roadmap-status-filter-skill/BE-0162-roadmap-status-filter-skill.md)) key on
`Status` alone. A fact recorded only in `Superseded by` stays invisible to both without opening every
file, which is exactly the friction this item removes.

**Name the new value `Proposal (rejected)`, matching `Proposal (deferred)`'s wrapped shape instead of
a bare word.** Considered, since it would have kept the two "not proceeding" values visually paired.
Not adopted: it would leave `Deferred` either still wrapped (an inconsistent match against the flat
`In progress` / `Proposal` / `Implemented` triple) or renamed to the bare `Deferred` while `Rejected`
stayed wrapped, two different shapes doing the same job. BE-0078 already chose the bare `In progress`
over the wrapped `Accepted, in progress` for the same reason; the bare `Deferred` / `Rejected` pair
extends that choice rather than reopening it.

**Derive `Rejected` automatically from a filled `Superseded by` field, instead of a hand-set `Status`
value.** Rejected: `Superseded by` cannot be the whole signal, since a `Proposal` or `In progress` item
can be rejected for a reason that names no successor BE at all — out of scope, or an unworkable design
against a prime directive. Keeping `Status` hand-set, like every other bucket, matches the existing
practice: nothing except `Implemented` is derived from another roadmap field.

**Leave the existing `Proposal (deferred)` items unmigrated, adding `Rejected` only for future
items.** Rejected: it would leave BE-0154, the concrete case that motivated this item, sitting exactly
where it started, misleading a reader the same way it does today. Migrating today's roadmap in the
same PR is what closes the actual gap.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Not started.

## References

- [BE-0078 — Status-driven roadmap folders](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md)
  — introduced the four-bucket `Status` vocabulary and the "the bucket is derived from `Status`"
  invariant this item extends to five, and set the precedent for flattening a wrapped status name to a
  bare one.
- [BE-0159 — Flatten roadmap status folders](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders.md)
  — made `Status` directory-independent, and is BE-0154's named successor, the concrete case
  motivating this item.
- [BE-0154 — Run roadmap-promote from the base SHA](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha.md)
  — the deferred item this item's migration reclassifies to `Rejected`.
- [BE-0100 — Roadmap progress-tracking template](../BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template.md)
  — defined the reciprocal `Related` / `Superseded by` fields this item reuses for the superseded
  trigger.
- [BE-0162 — Roadmap status-filter skill](../BE-0162-roadmap-status-filter-skill/BE-0162-roadmap-status-filter-skill.md)
  — the `roadmap-filter` skill that keys on `Status`, cited under *Alternatives considered*.
- [`docs/ai-development.md`](../../docs/ai-development.md#roadmap-items-be-ids-strict) — the
  Status→bucket table and the roadmap metadata rules this item revises.

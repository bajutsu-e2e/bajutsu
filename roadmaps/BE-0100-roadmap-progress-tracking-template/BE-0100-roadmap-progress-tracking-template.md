**English** · [日本語](BE-0100-roadmap-progress-tracking-template-ja.md)

# BE-0100 — Progress tracking and cross-item relations in the BE template

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0100](BE-0100-roadmap-progress-tracking-template.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0100") |
| Implementing PR | [#415](https://github.com/bajutsu-e2e/bajutsu/pull/415) |
| Topic | Contributor workflow |
| Related | [BE-0074](../BE-0074-be-template-standardization/BE-0074-be-template-standardization.md) |
<!-- /BE-METADATA -->

## Introduction

A roadmap item records *what* to build and *why*, but not *how far along* the work is. The state of
an item — which slices have shipped, what remains, what changed when — lives outside the file: in the
`Implementing PR` row, in commit history, in a contributor's head. Two items
([BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle.md),
[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md))
grew an ad-hoc `### Implementation status` subsection to fill the gap, but the shape was theirs alone
and nothing kept it current.

This item proposes extending the canonical template
([BE-0074](../BE-0074-be-template-standardization/BE-0074-be-template-standardization.md))
with one new section, **`## Progress`**, and two optional metadata fields, **`Related`** and
**`Superseded by`**. The Progress section is a *living* section — a MECE checklist mirroring the work
breakdown, plus a short chronological log — kept current as work proceeds. The metadata fields record
cross-item links: the items this one builds on or relates to, and the item that invalidates it. It
also pins a norm that already holds informally: **`Detailed design` enumerates the work MECE**. The
whole change is documentation, a scaffolder, and a format check — no tool behaviour, no LLM on any
path.

## Motivation

The roadmap is the project's shared planning surface, but it answers "is this done?" only at the two
extremes its `Status` encodes — `Proposal` (nothing) and `Implemented` (everything). An item
mid-flight has no structured place to say *which* part shipped. The consequences are concrete:

- **Progress is invisible or bespoke.** A reader cannot tell from an `In progress` item how much of
  it is real without reading the prose end to end and cross-referencing the PRs. The two items that
  do record it invented their own `### Implementation status` subsection — useful, but unenforced and
  inconsistent, so the next author has nothing to copy from.
- **The work is not always laid out as a complete set.** Some Detailed-design sections sketch an
  approach rather than enumerating the units of work, so there is no baseline to check progress
  against — you cannot tick boxes that were never drawn.
- **Cross-item relationships live only in prose.** When a later item supersedes or relates to an
  earlier one, the link (if made at all) sits in a `References` bullet on one side only, so it is
  neither reciprocal nor machine-greppable.

[BE-0074](../BE-0074-be-template-standardization/BE-0074-be-template-standardization.md)
already established the project's answer to "the template drifts": pin the shape and gate it. That
item fixed five sections and a metadata field set, and deliberately scoped progress *out*. This item
adds the missing piece, on the same machinery, so "how far along" becomes a first-class, gate-checked
part of every item.

## Detailed design

The implementation is four disjoint pieces — the template surface, the tooling, the prose, and the
retrofit of existing items. **The retrofit is part of this item's implementation, done in the
implementing PR — not in this proposal.**

### 1. The template: one new section and two new fields

A sixth H2 section, **`## Progress`** (`## 進捗`), is inserted between `## Alternatives considered`
and `## References` — after the design rationale, before the reference links. It holds, in order, a
checklist mirroring the MECE work breakdown in `Detailed design` (one `- [ ]` box per unit of work,
ticked `- [x]` as it lands), then a short chronological log of what changed and when (oldest first),
linking the PRs. The seeded skeleton:

```markdown
## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] TBD — enumerate the work breakdown (MECE) here once scoped.
```

Two optional metadata fields are added, in canonical order after `Topic` and before `Origin`:
`Related` (`関連`) — items this one builds on or relates to — and `Superseded by` (`無効化`) — the
item that invalidates this one (its successor). Both are links; both optional, so existing items need
no field added. The link is **reciprocal**: the superseding item lists the other under `Related`, the
superseded one names its successor under `Superseded by`.

### 2. The norm: MECE work breakdown, a living Progress section

Two rules go into `CLAUDE.md`, `roadmaps/README.md` (+ `-ja`), and `docs/ai-development.md`:

- **`Detailed design` enumerates the work MECE** — mutually exclusive, collectively exhaustive — so
  the checklist has a complete, non-overlapping set to mirror.
- **`Progress` is kept current as work proceeds.** Every PR that advances an item ticks its boxes and
  adds a log entry in the same change, exactly as it fills the `Implementing PR` row. A not-yet
  -started `Proposal` carries a single placeholder box; an `Implemented` item carries the all-done
  checklist pointing at its `Implementing PR`.

These are review-enforced, not machine-enforced: a checker can confirm the section *exists* (below)
but not that a prose breakdown is genuinely exhaustive or that the boxes are honest.

### 3. The tooling

- **Scaffolder** ([`scripts/new_roadmap_item.py`](../../scripts/new_roadmap_item.py)) emits the
  `Progress` section seeded with the skeleton above, rather than a bare `TBD`.
- **Format check** ([`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py)) adds
  `Progress` / `進捗` to the required, ordered H2 headings, and `Related` / `Superseded by` (and their
  Japanese labels) to the known optional metadata fields. The index generator reads metadata by field
  name, so the new optional fields do not affect it.

Because the format check would then require the new section on *every* item, the test change and the
retrofit (below) must land together in the implementing PR — the gate cannot go green with one
without the other.

### 4. The retrofit (implementing PR)

Every existing item gains a `## Progress` section, honestly and without fabricated history:

- **Implemented** items get a one-line all-done checklist pointing at the `Implementing PR` — no
  invented day-by-day timeline.
- **In progress** items get a real checklist reflecting their actual state (migrating the two
  `### Implementation status` subsections), boxes ticked only where a slice demonstrably shipped.
- **Proposal / deferred** items get a single placeholder box, to be enumerated when picked up.

### Prime-directive compliance

The change is documentation plus a deterministic scaffolder and checker. No LLM enters any path; the
`run` / CI gate is untouched; nothing is app- or backend-specific.

## Alternatives considered

- **An H3 `### Implementation status` under `Detailed design`** (the existing organic practice).
  Rejected as the standard form: H3s are invisible to the format gate, so "every item has it, kept
  current" could not be pinned, and progress reads more naturally as a top-level section than buried
  in the design.
- **Two sections, `Work plan` and `Progress log`.** Rejected: the checklist *is* the work plan, so a
  single `Progress` section holding the checklist plus the log avoids duplicating the breakdown and
  adds one heading to retrofit instead of two.
- **A `Progress` metadata field (a percentage or status word) rather than a section.** Rejected: a
  scalar cannot carry a per-unit checklist or a log, and it invites a fake precision the checklist
  makes concrete.
- **A machine-checked "is it current?" rule.** Rejected as infeasible: no deterministic check can
  tell whether a prose breakdown is exhaustive or a box is honestly ticked. The gate checks the
  section *exists*; currency is a review norm, like docstring quality.
- **Reconstructing a full timeline for shipped items.** Rejected on honesty grounds: inventing a
  retrospective day-by-day history for the implemented items would fabricate a record. They get a
  truthful one-liner pointing at the `Implementing PR` instead.

## Progress

- [x] Template surface — `## Progress` added between `Alternatives considered` and `References`, plus the optional `Related` / `Superseded by` metadata fields, in the format gate (`tests/test_roadmap_format.py`).
- [x] The norm — the MECE-work-breakdown rule and the living-`Progress` rule documented in `CLAUDE.md`, `roadmaps/README.md` (+ `-ja`), and `docs/ai-development.md`.
- [x] Tooling — the scaffolder (`scripts/new_roadmap_item.py`) seeds the `Progress` skeleton; the format check requires the section and accepts the new fields. Covered by `tests/test_new_roadmap_item.py`.
- [x] Retrofit — every existing item gained a `## Progress` section: all-done checklists for implemented items, the existing `### Implementation status` subsections folded in for in-progress items, and placeholder boxes for proposals / deferred.

All four pieces shipped together in [#415](https://github.com/bajutsu-e2e/bajutsu/pull/415) — the template, the gate and scaffolder, the norm docs, and the retrofit had to land in one PR, since the format check requires the new section on every item.

## References

- [BE-0074 — Standardize the BE item template (EN / JA)](../BE-0074-be-template-standardization/BE-0074-be-template-standardization.md) — the template-pinning item this one extends from five sections to six; BE-0074 carries the reciprocal `Related` back-link.
- [BE-0043 — Conflict-resistant file flow](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md) — the "make the invariant machine-checked" precedent.
- [BE-0078 — Status-driven roadmap folders](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md) — `Status` as the lifecycle source of truth, which `Progress` complements with finer-grained state.
- [`scripts/new_roadmap_item.py`](../../scripts/new_roadmap_item.py) · [`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py) — the scaffolder and the gate the implementing PR extends.
</content>

**English** · [日本語](BE-0078-roadmap-status-folders-ja.md)

# BE-0078 — Status-driven roadmap folders (proposals / deferred / in-progress / implemented)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0078](BE-0078-roadmap-status-folders.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0078") |
| Implementing PR | [#220](https://github.com/bajutsu-e2e/bajutsu/pull/220) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

A roadmap item's `Status` already takes one of **four** values — `Proposal`, `Proposal (deferred)`,
`Accepted, in progress`, `Implemented` — but the repository files every item under only **two**
folders, `roadmaps/proposals/` and `roadmaps/implemented/`. The folder axis is two notches too
coarse, so two distinctions the metadata already records are lost the moment an item is written to
disk: an item *being built* sits in `proposals/` next to one that is merely *proposed*, and a
*parked* (deferred) proposal sits there too, indistinguishable from a live one. Nine items are
in-progress under `proposals/` today (BE-0026, BE-0038, BE-0041, BE-0048, BE-0049, BE-0050, BE-0052,
BE-0054, BE-0068), and two are deferred (BE-0027, BE-0040).

This item makes the folder layout a faithful, one-to-one image of `Status`. It (1) renames the
middle status to `In progress` / `実装中`, (2) gives **each** status its own folder — `proposals/`,
`deferred/`, `in-progress/`, `implemented/` — and (3) reorganises the index into four top-level
buckets in the same order, so the page a reader scans matches the on-disk layout exactly. The whole
surface is documentation, two scripts, and the format/index tests; it adds no LLM to any path and
changes no runner, driver, or `serve` behaviour, so it touches none of the prime directives.

## Motivation

The roadmap quietly carries **three independent axes** today, and they disagree on how finely a
lifecycle is divided:

| Axis | Values | What it drives |
|---|---|---|
| `Status` | `Proposal` · `Proposal (deferred)` · `Accepted, in progress` · `Implemented` | the source of truth |
| `Track` | `Accepted` · `Proposals` | the index's top-level grouping |
| Folder | `proposals/` · `implemented/` | the item's on-disk home and link path |

`Status` is the finest axis (four values) and is treated as the source of truth —
`promote_roadmap_items.py` says so in its own docstring. Yet the folder it derives is the coarsest
(two values), mapping *only* `Implemented` to `implemented/` and *everything else* to `proposals/`.
So `proposals/` is really three different things at once: live proposals, parked (deferred)
proposals, and items with a PR in flight. The daily friction is that browsing `proposals/` — on
disk or in the index — cannot tell "half-built" from "not started" from "shelved" without opening
each file's `Status`.

The middle and parked states are not fringe cases: nine of the project's most active items are
in-progress and two are deferred. The information to separate them already exists in `Status`; only
the folder layout flattens it.

This is the same class of problem the contributor-workflow line has been closing all along — make
the structure carry the invariant rather than leaving it to prose.
[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
reshaped the file flow so independent changes touch disjoint files;
[BE-0074](../BE-0074-be-template-standardization/BE-0074-be-template-standardization.md)
pinned the item template and the `Status` vocabulary with a gate test. This item continues that
line by making each `Status` its own folder, so the layout expresses the distinctions the metadata
already records.

## Detailed design

### Status and its four homes

`Status` stays the single source of truth, and each value maps to exactly one folder and one index
bucket — a bijection:

| Status (EN / JA) | Folder / index bucket |
|---|---|
| `Proposal` / `提案` | `roadmaps/proposals/` |
| `Proposal (deferred)` / `提案（保留）` | `roadmaps/deferred/` |
| `In progress` / `実装中` | `roadmaps/in-progress/` |
| `Implemented` / `実装済み` | `roadmaps/implemented/` |

`Proposal` and `Proposal (deferred)` are siblings — both are proposals, one live and one shelved —
so they are *adjacent* buckets, not a parent/child. Splitting them lets a reader see at a glance
what is actively under consideration versus what has been parked, which is the whole reason the
`Deferred` distinction exists.

### Renaming the middle status

`Accepted, in progress` becomes `In progress`; `可決・実装中` becomes `実装中`. This aligns the three
names that should agree — the status word, the folder name, and the index heading all read
"in progress" — and drops the now-redundant "Accepted," framing (an item being implemented was
necessarily accepted). The change touches the nine in-progress item pairs (the `Status` value, both
languages), the `STATUS_PAIR` map in
[`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py), the `status_display` maps in
[`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) (the index already
renders the middle status as "In progress" / "実装中"; the raw key changes), and the prose that lists
the status set ([`CLAUDE.md`](../../CLAUDE.md),
[`docs/ai-development.md`](../../docs/ai-development.md), both README index pages).

### Status is the single source of truth — retire `Track`

The folder is *already* derived from `Status`. The one axis still set by hand is `Track`, and with a
four-way bucketing it becomes a pure restatement of `Status`: `Implemented` ⇒ Implemented,
`In progress` ⇒ In progress, `Proposal` ⇒ Proposals, `Proposal (deferred)` ⇒ Deferred. A
hand-maintained field that merely echoes `Status` is exactly the drift surface BE-0043 and BE-0074
work to remove.

The recommended design therefore **retires the `Track` field** and derives the index bucket from
`Status`. In `build_roadmap_index.py` the section key changes from `(track, topic)` to
`(bucket(status), topic)`, where `bucket` is the mapping in the table above; `Track` is dropped from
the metadata schema and from the field order/required set in `test_roadmap_format.py`. Every item
file loses one metadata row, and `Status` becomes the lone field that decides both an item's folder
and its index section — the two can no longer disagree. (A lower-churn alternative that keeps `Track`
is weighed under *Alternatives considered*.)

### Reorganising the index

The two top-level headings (`## Accepted` / `## Proposals`, and their Japanese
`## 可決済み` / `## 提案`) become four, ordered most-progressed first, with the parked bucket last:

- EN: `## Implemented` · `## In progress` · `## Proposals` · `## Deferred`
- JA: `## 実装済み` · `## 実装中` · `## 提案` · `## 保留`

`Topic` is unchanged — it remains the secondary grouping inside each bucket. The `SECTIONS` table in
`build_roadmap_index.py` is re-keyed by bucket: a topic that currently spans buckets splits, each
part under its own bucket with its own `<!-- GENERATED:* -->` marker pair. Two topics split across
the new Deferred bucket today — *Miscellaneous / on hold* (its deferred member BE-0027 moves to the
Deferred bucket; the live BE-0028 stays under Proposals) and *Candidates from competitive research
(MagicPod / Autify)* (BE-0040 moves; BE-0037 / BE-0046 stay). The per-topic prose outside the
markers is preserved; the "Accepted" framing paragraphs are rewritten for the Implemented /
In progress split, and the "Miscellaneous / **on hold**" topic name can shed "on hold" now that
Deferred is its own bucket.

### Folder migration

Eleven item directories move: the nine `In progress` items and the two `Deferred` items
(`git mv roadmaps/proposals/<dir> roadmaps/in-progress/<dir>` and `…/deferred/<dir>`). The
`Implemented` items already live in `implemented/` and the live `Proposal` items stay in
`proposals/`, so neither moves. The index **table rows** regenerate automatically —
`build_roadmap_index.py` derives each link's path from the folder an item now sits in — so no index
link is hand-edited.

What must be fixed by hand are the **hand-written links** to moved items that live outside the
generated regions, because nothing rereads them. These all point at the nine in-progress items; the
two deferred items have **no** references outside the auto-regenerated tables:

- the two README section-intro links (BE-0038 and BE-0041) in each language;
- roughly a dozen `docs/` references (and their `docs/ja/` mirrors): `cli.md`, `multi-platform.md`,
  `drivers.md`, and `scenarios.md` all point at `roadmaps/proposals/BE-00xx-…` for one of the nine.

This repository has **no link checker** in the gate, so a stale `proposals/…` path will not fail
`make check` — it just 404s for a reader. The implementation must sweep these paths in the same
change (a scripted rewrite over the known slugs is enough), and adding a roadmap-link check to the
gate is a reasonable companion (it fits the *executable contributor guardrails* direction of
[BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)).

### Scripts and tests touched

- [`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py) — `CATEGORIES`
  grows to four; `expected_category(status)` returns the four-way mapping. The script's job is
  unchanged (reconcile each item's folder with its `Status`); it simply now has four targets. Its
  gate counterpart `tests/test_promote_roadmap_items.py` follows.
- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) — `CATEGORIES` grows to
  four; `status_display` keys rename; `SECTIONS` re-keyed by bucket; `Track` parsing removed (or
  repurposed under the alternative). Its gate counterpart `tests/test_roadmap_index.py` follows.
- [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) — `CATEGORIES` grows
  to four so it counts existing IDs across all folders; `PLACEHOLDER_CATEGORY` stays `proposals`
  (a brand-new item is always a live proposal first).
- [`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py) — `CATEGORIES` grows to
  four; `STATUS_PAIR` renames the middle pair; `Track` leaves the required-field set and field order
  (recommended design).

### Prime-directive compliance

The change is documentation, two generator/checker scripts, and their gate tests. No LLM is added to
any path; `run` and CI stay deterministic; nothing app-specific moves into the tool. It is a
contributor-workflow refactor, in the same family as BE-0043 / BE-0061 / BE-0074.

## Alternatives considered

- **Keep two folders; only rename the status and regroup the index visually.** Rejected: the folder
  coarseness *is* the friction. Leaving in-progress and deferred items physically under `proposals/`
  keeps the exact flattening this item exists to remove.
- **Add an `in-progress/` folder only, and keep deferred inside `proposals/`** (three folders).
  This is the smaller change and defensible — a deferred item *is* a parked proposal, so it could
  share the proposals folder. Rejected here in favour of the full bijection: giving every `Status`
  its own folder lets a reader find "what's parked" directly, on disk and in the index, without
  opening files, and it keeps the rule uniform (one status, one folder) rather than special-casing
  one status to share another's home.
- **Keep the `Accepted, in progress` label.** Rejected: a folder named `in-progress/` paired with a
  status reading "Accepted, in progress" reintroduces the name/concept seam this item removes.
  `In progress` makes the status, folder, and heading read alike.
- **Keep `Track`, expanded to four values**, instead of deriving the bucket from `Status`. Lower
  churn for the generator (it stays keyed on `(track, topic)`) and it keeps the metadata schema
  BE-0074 pinned. Not recommended: with four buckets `Track` is a pure duplicate of `Status`, so it
  reintroduces the hand-maintained drift surface the *folder-from-Status* invariant already avoids —
  and it still requires editing the `Track` value on every item. Deriving from `Status` is the
  cleaner end-state; this is the conservative fallback if the schema change is judged too large for
  one PR.

## Progress

- [x] Shipped — see the *Implementing PR* above.
- **Historical note:** this item's bucket-from-`Status` design still holds, but the render target
  it describes — the four `## Implemented` / `## In progress` / `## Proposals` / `## Deferred`
  headings and their `<!-- GENERATED:* -->` marker pairs in `README.md` / `README-ja.md` — was
  retired by [#1257](https://github.com/bajutsu-e2e/bajutsu/pull/1257) in favor of the roadmap
  dashboard, which classifies every item into the same four buckets this item defines.

## References

- [`CLAUDE.md`](../../CLAUDE.md) — the roadmap status/folder rules this item revises.
- [`roadmaps/README.md`](../README.md#adding-a-roadmap-item--be-ids) — the
  prose description of the two-folder model that becomes four.
- [`docs/ai-development.md`](../../docs/ai-development.md) — the contributor guide's Status→Track
  table and the "moves its directory" lifecycle prose, both updated by this item.
- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py),
  [`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py),
  [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) — the three scripts
  that hardcode the two-folder assumption, plus `tests/test_roadmap_format.py` /
  `tests/test_roadmap_index.py` / `tests/test_promote_roadmap_items.py` that gate it.
- [BE-0074 — Standardize the BE item template](../BE-0074-be-template-standardization/BE-0074-be-template-standardization.md)
  — pinned the `Status` vocabulary and the metadata schema (`Track` included) this item revises.
- [BE-0043 — Conflict-resistant file flow](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md),
  [BE-0061 — Collision-proof BE-ID allocation](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
  — the contributor-workflow siblings whose "structure carries the invariant" approach this follows.
- [BE-0069 — Executable contributor guardrails](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
  — where an optional roadmap-link check (to catch stale `proposals/…` paths after a move) would fit.

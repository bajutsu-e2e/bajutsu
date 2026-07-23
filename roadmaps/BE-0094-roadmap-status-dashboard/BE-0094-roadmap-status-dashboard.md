**English** · [日本語](BE-0094-roadmap-status-dashboard-ja.md)

# BE-0094 — Generated roadmap status dashboard on GitHub Pages

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0094](BE-0094-roadmap-status-dashboard.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0094") |
| Implementing PR | [#322](https://github.com/bajutsu-e2e/bajutsu/pull/322) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

A roadmap dashboard page, published to GitHub Pages alongside the existing API reference, that
renders every BE item as cards grouped by lifecycle bucket (Implemented / In progress / Proposals /
Deferred) and by Topic. The page is generated from each item's own metadata on every docs build, so
it always reflects the committed roadmap and can never go stale.

## Motivation

The roadmap already has a single source of truth — the metadata block in each
`roadmaps/<category>/BE-NNNN-<slug>/` item — and a generated text index
([BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md) regenerates
the `roadmaps/README.md` tables from it. That index is exhaustive but dense: a flat set of tables you
read top to bottom. Two questions it answers slowly are the ones contributors ask most — *what is in
flight right now?* and *how is work distributed across topics?* Answering them today means scanning
several tables and counting by hand.

A dashboard answers both at a glance: counts per bucket up top, then cards grouped by lifecycle and
topic, each linking to its full proposal. It is the visual front end to the same data the index
already governs — no new source of truth, just a second rendering of the existing one, tuned for
"see the shape of the roadmap" rather than "look up one row."

The constraint that makes this safe is the same one the index lives under: **the page is derived,
never authored.** A hand-maintained status board drifts the moment an item ships and nobody updates
the board. Deriving the page from the item metadata on every build removes that failure mode
entirely — the only way to change the dashboard is to change an item's metadata, which is exactly
where the change belongs.

## Detailed design

**Source of truth.** The generator reuses the index generator's loader
(`scripts/build_roadmap_index.py`'s `load_items` / `BUCKETS` / `TOPICS`), so the dashboard and the
index parse the roadmap identically and can never disagree about an item's bucket or topic. An item's
lifecycle bucket is derived from its `Status`
([BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md)),
never a hand-set field.

**Build artifact, never committed.** `scripts/build_roadmap_dashboard.py` writes
`docs/api/roadmap.md` — a self-contained HTML page (inline styles, all data inlined) — which is
gitignored, exactly like the generated API reference `site/`. This is the load-bearing choice:

- It **cannot drift.** `make docs` / `make docs-serve` regenerate it first, and the `docs` workflow
  regenerates it before publishing, so the published page always matches the committed roadmap.
- It is **decoupled from BE-id allocation.** Placeholder items carry the literal `BE-0094` id until
  CI assigns the real number on a PR ([BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)).
  The directory regex (`^BE-\d{4}-`) skips `BE-0094`, so the placeholder is excluded from the
  dashboard just as it is from the index — and because the page is never committed, nothing has to be
  re-rendered when allocation later rewrites `BE-0094` to `BE-NNNN`.

**Rendering.** Cards are grouped category-major (by Topic, in the index's topic order). Each card
carries its own status as a colour and a badge (Implemented / In progress / Proposal / Deferred) and
an `Origin` note where the metadata carries one, and links to the item's English file on GitHub. Each
category heading shows a progress figure and a stacked bar of its status composition (see below). A
category whose items are *all* Implemented is moved to a separate **Completed** group below the rest,
so the main view is the work still in flight. The page is added to the MkDocs `nav`, so it sits in
the existing Pages site rather than standing up new hosting.

**Per-category progress.** Each category shows the share of its items that are Implemented — the
count of `Implemented` items over the category's total — as a percentage, beside a stacked bar whose
segments are the category's counts of each status. This is the one figure that quantifies progress,
and it is derived **purely from the `Status` field** the roadmap already governs, so it has a source
of truth: it is a fact about the item set, not an estimate. The breakdown (`N/M implemented`) is shown
next to the percentage so the number is transparent.

**Interactivity (progressive enhancement).** A single tiny inline script — the only JavaScript on the
page — adds the interaction. On load it collapses every category to a compact overview (just the
heading and its progress bar), so the landing view is the shape of the whole roadmap at a glance.
From there: clicking a heading expands that category's cards; the summary counts double as **status
checkboxes**, each an independent on/off control (all checked by default). Unchecking one removes that
status's cards everywhere and hides any category or group left empty; re-checking every status
returns to the collapsed overview. The collapsed state is applied *by the script*, never baked into
the markup — so with scripting off every status is on, every category stays open, every card is
visible, and the page is fully readable without it. Nothing fetches or computes; it only shows and
hides already-rendered markup.

**Honesty of the data.** Only facts the metadata carries are shown — status, Topic, Origin, title,
and counts — plus the per-category progress derived from them. The line not crossed is a *per-item*
completion figure ("BE-0041 is 85% done"): no such number lives in any item's metadata, so inventing
one at render time would put a figure with no source of truth on an official page. The per-category
percentage is different in kind: it is `Implemented / total` over the category, a fact about the
status counts, not an estimate. A reader who wants finer progress follows the card to the item, where
the `Implementing PR` row and the prose tell the real story.

**Prime-directive fit.** This is documentation tooling: it touches no driver, no runner, and no
run/CI gate, and introduces no LLM anywhere. It is a pure, deterministic function of the roadmap
metadata.

### Enabling Pages

The `docs` workflow already builds the site on every `main` push and deploys on a manual run; GitHub
Pages must be enabled once (Settings → Pages → source "GitHub Actions") for the dashboard — and the
API reference — to be served. That one-time repository setting is outside this change.

## Alternatives considered

- **A committed, drift-checked page (like the index).** Rejected: committing the rendered page would
  couple it to the BE-id-allocation machinery — when CI rewrites `BE-0094` to `BE-NNNN` it would also
  have to re-render and re-commit the page, or the drift check would fail. Generating it as a build
  artifact removes that coupling and the whole class of "forgot to regenerate" failures.
- **A standalone dashboard site/URL.** Rejected: a second Pages target is more infrastructure for no
  benefit. One page in the existing MkDocs site reuses the workflow that already exists and keeps the
  roadmap one click from the API reference.
- **A static, hand-maintained HTML snapshot.** Rejected outright: it goes stale the moment any item
  ships or changes status, which defeats the entire point of an always-current dashboard.
- **A per-item completion percentage.** Rejected: no such figure lives in the metadata, so it would
  be invented at render time — a number with no source of truth on a published page. The per-category
  progress that *is* shown is the opposite: a straight count of `Implemented` items over the
  category total, derived entirely from the `Status` field.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[`roadmaps/README.md`](../README.md), `scripts/build_roadmap_dashboard.py`,
`scripts/build_roadmap_index.py`, [`mkdocs.yml`](../../mkdocs.yml),
[`.github/workflows/docs.yml`](../../.github/workflows/docs.yml),
[BE-0043 — Roadmap index merge driver](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md),
[BE-0061 — Atomic BE-id allocation](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md),
[BE-0078 — Status-derived roadmap folders](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md)

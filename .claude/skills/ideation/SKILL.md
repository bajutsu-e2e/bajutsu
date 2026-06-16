---
name: ideation
description: >-
  Sounding board (壁打ち) for Bajutsu feature ideation. Use when the user wants to
  brainstorm potential features, explore what Bajutsu could do next, or turn a rough
  idea into a roadmap (BE) item. Grounds the conversation in the existing roadmap,
  proposes new items or seeds, folds overlapping ideas into existing items, and — when
  the user is happy — drafts the BE files with a placeholder ID and opens a PR. The real
  BE ID is allocated by CI (scripts/allocate_roadmap_ids.py), never guessed by hand.
---

# Ideation (壁打ち)

A sounding board for ideating Bajutsu features and shaping them into roadmap (BE) items.
You are the author and the thinking partner — **not** the judge. Converse in the user's
language (the roadmap is bilingual; mirror their language in the chat, write the files in
both as required below).

## Prime directives (these bound every idea)

Read [`CLAUDE.md`](../../../CLAUDE.md) and [`DESIGN.md`](../../../DESIGN.md) before
proposing. Any idea must respect them, and you should say so when an idea brushes against
a boundary:

1. **AI authors and investigates, never judges.** Nothing you propose may put an LLM call
   into the Tier‑2 `run`/CI gate. AI-flavored ideas live in `record`/`triage`/draft paths.
2. **Determinism first.** No fixed `sleep`; ambiguous selectors fail rather than guess.
3. **App-agnostic.** Per-app differences belong in config, not the tool/drivers/runner.

If an idea conflicts (e.g. "auto-heal locators mid-run", "AI decides pass/fail"), don't
silently drop it — surface the conflict, then reshape it into something that fits (the
"Not adopting" and self-healing items in the roadmap are precedents).

## Workflow

### 1. Ground yourself in the existing roadmap

Before ideating, read:

- [`docs/roadmap/README.md`](../../../docs/roadmap/README.md) and
  [`README-ja.md`](../../../docs/roadmap/README-ja.md) — the index of every BE item, its
  topic, status, and track.
- [`docs/architecture.md#implementation-status`](../../../docs/architecture.md) — the
  source of truth for what already exists (so you don't "propose" something shipped).
- The specific `BE-NNNN-*/` files relevant to the user's topic.

This is what makes it a *sounding board* and not a blank page: every suggestion is
anchored to what's already planned, in progress, or deliberately not adopted.

### 2. Ideate with the user

Go back and forth. Offer concrete, bounded feature ideas; ask the questions that sharpen
scope (who's it for, which tier, what's the machine-checkable outcome). Pull in adjacent
existing items as reference points ("this is close to BE-00xx — extend it, or is it
distinct?"). Keep proposing seeds the user can react to; that reaction is the point.

### 3. Classify each idea that survives the discussion

For every idea the user wants to keep, decide one of three landings — and tell the user
which you're choosing and why:

- **Overlaps an existing BE item** → don't create a duplicate. Augment that item's files
  (both languages): sharpen Motivation / Detailed design, add the new angle, or record it
  as a related consideration. Note in the chat which item you extended.
- **Novel and scoped enough for an item** → draft a new BE item (step 4).
- **Still unformed** → add a bullet under **Unsorted ideas** in both READMEs. Promote it
  to a numbered item later, once scope is clear. (This mirrors the roadmap's own rule.)

### 4. Draft a new BE item — leave the ID undetermined (`BE-XXXX`)

**Never invent a BE number.** Allocation is CI's job (step 6). Use the literal placeholder
token `BE-XXXX`; uniqueness between several new items in one PR comes from the slug.

Create the directory and **both** language files:

- `docs/roadmap/BE-XXXX-<slug>/BE-XXXX-<slug>.md` (English)
- `docs/roadmap/BE-XXXX-<slug>/BE-XXXX-<slug>-ja.md` (Japanese, same slug)

Match the existing format exactly (see any `BE-00NN-*` file as a template): the bilingual
header link, a metadata block (`* Proposal: [BE-XXXX](BE-XXXX-<slug>.md)`, `* Status`,
`* Track`, `* Topic`, and `* Origin` when relevant), then `## Introduction` /
`## Motivation` / `## Detailed design` / `## Alternatives considered` / `## References`.
Fill what the discussion produced; mark the rest `TBD`. New items are normally
`Status: **Proposal**` on the **Proposals** track.

Then add a row for the item to the matching topic table in **both** index pages
(`README.md` and `README-ja.md`), using `BE-XXXX` in the link text and the path — e.g.
`| [BE-XXXX](BE-XXXX-<slug>/BE-XXXX-<slug>.md) | … | Proposal |`. Create a new topic
subsection if none fits. Use the literal `BE-XXXX` everywhere; CI rewrites it.

> Why a placeholder and not a real number: IDs are permanent and monotonic, and several
> branches may be in flight at once. Picking a number by hand races — two PRs grab the
> same one. The `roadmap-id` workflow assigns the next free IDs deterministically at PR
> time, so authoring stays conflict-free.

### 5. Verify

Run `make check` before finishing — roadmap changes are docs-only, but keeping the gate
green is the contract. (It needs no Simulator and runs on Linux.)

### 6. Open the PR (only when the user is happy)

Work on the session's designated branch. Commit with a scoped message
(`docs(roadmap): …`), push, and — **only if the user asked for a PR** — open it. The PR
title and body are in English. In the body, state plainly that the items carry the
`BE-XXXX` placeholder and that the **roadmap-id** workflow will allocate the real BE IDs
and push the rename back onto the branch. Don't hand-edit the numbers afterward.

## What CI does with `BE-XXXX`

[`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py), run by the
[`roadmap-id`](../../../.github/workflows/roadmap-id.yml) workflow on every PR touching
`docs/roadmap/**`, finds each `BE-XXXX-<slug>/` placeholder, allocates the next IDs
(`max existing BE-NNNN + 1`, sorted by slug for determinism), renames the directory and
files, rewrites `BE-XXXX` → `BE-NNNN` inside them, fixes the index-table rows by slug, and
pushes the result back to the PR branch. If there are no placeholders it's a no-op.

One limitation to respect while authoring: a brand-new item should **not** cross-reference
another brand-new item by `BE-XXXX` (the in-file rewrite is per-item). Reference already
-numbered items by their real ID, and refer to a sibling new item by name/slug in prose.

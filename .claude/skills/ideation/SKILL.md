---
name: ideation
model: sonnet
description: Turn a rough Bajutsu feature idea into a bilingual roadmap proposal. Use for proposal authoring and review, never implementation.
---

# Ideation

A sounding board for ideating Bajutsu features and shaping them into roadmap (BE) items.
You are the author and the thinking partner — **not** the judge. Converse in the user's
language (the roadmap is bilingual; mirror their language in the chat, write the files in
both as required below).

## Scope: roadmap authoring only — never implement

This skill **only** authors and shapes roadmap (BE) items. It stops at the roadmap files
(and, when asked, the PR that carries them). **Do not write, modify, or refactor any
product code** — not `bajutsu/`, not `BajutsuKit/`, not tests, not config, not demos — even
if the discussion makes the implementation obvious or the user nudges toward "just build
it". Your deliverable is always the BE proposal, never a working feature.

If the user asks you to implement an idea, don't switch hats mid-session: point them to the
[`implement-be`](../../../.apm/skills/implement-be/SKILL.md) skill (the deterministic counterpart that ships an
existing BE item from its ID) and keep this session to authoring the proposal — or, when the
item is small and its design is already settled, to
[`propose-and-build`](../../../.apm/skills/propose-and-build/SKILL.md), which authors the proposal and implements
it together in one BE-creation PR. The only files *this* skill touches are under
`roadmaps/` (plus the index the generator regenerates).

## Prime directives (these bound every idea)

Read [`AGENTS.md`](../../../AGENTS.md) and [`DESIGN.md`](../../../DESIGN.md) before
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

Alongside the steps below, keep [`be-progress-tracker`](../../../.apm/skills/be-progress-tracker/SKILL.md)
current: at minimum after step 4 (the item exists, even still as `BE-XXXX`), step 5 (the self-review
pass comes back clean), and step 6 (`make check` is green). Invoke it through the Agent tool with
`model: "haiku"` passed explicitly, since a subagent call does not inherit that skill's own
frontmatter model. It only turns decisions this workflow already made into a glanceable status page — never
let it gate or slow this workflow down; skip a checkpoint rather than block on it.

### 1. Ground yourself in the existing roadmap

Before ideating, read:

- [`roadmaps/README.md`](../../../roadmaps/README.md) and
  [`README-ja.md`](../../../roadmaps/README-ja.md) — the index of every BE item, its
  topic, status, and track.
- [`docs/architecture.md#implementation-status`](../../../docs/architecture.md) — the
  source of truth for what already exists (so you don't "propose" something shipped).
- The specific `BE-NNNN-*/` files relevant to the user's topic.

This is what makes it a *sounding board* and not a blank page: every suggestion is
anchored to what's already planned, in progress, or deliberately not adopted.

### 2. Ideate with the user

Go back and forth. Offer concrete, bounded feature ideas; ask the questions that sharpen
scope (who's it for, which tier, what's the machine-checkable outcome). Ask one more that the
machine-checkable question does not cover: what would the user point to, once the change has
shipped, to say it worked? A machine-checkable outcome is the deterministic assertion a feature
is gated on; this question asks for the observable difference the item's Motivation has to name,
and step 4 carries the answer into that prose
([BE-0383](../../../roadmaps/BE-0383-motivation-verifiable-outcome/BE-0383-motivation-verifiable-outcome.md)).
Pull in adjacent existing items as reference points ("this is close to BE-00xx — extend it, or
is it distinct?"). Keep proposing seeds the user can react to; that reaction is the point.

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

**Never invent a BE number.** Allocation is CI's job (step 7). Scaffold the item with the
command rather than authoring the files by hand — it emits the literal `BE-XXXX` placeholder,
the exact canonical format, and skips the index (so the gate stays green locally):

```
make new-roadmap-item SLUG=<slug> TITLE="<title>" [TOPIC="<topic>"] [STATUS=Proposal] [HANDLE=<handle>]
```

This creates `roadmaps/BE-XXXX-<slug>/` with both `BE-XXXX-<slug>.md` and its `-ja.md`
mirror — the bilingual header link, the metadata block (`Proposal` / `Author` / `Status` /
`Topic`), and the five sections (`Introduction` / `Motivation` / `Detailed design` /
`Alternatives considered` / `References`) seeded with `TBD`. `TOPIC` is validated against the
index's known topics; `HANDLE` is the author's GitHub handle (defaults from `git config`).

Then **fill the `TBD` sections** with what the discussion produced. Before you draft that prose,
invoke the [`document-writing`](../../../.apm/skills/document-writing/SKILL.md) skill — it is the authoritative norm for BE
prose in both languages ([BE-0278](../../../roadmaps/BE-0278-tech-writing-skill/BE-0278-tech-writing-skill.md)),
and it shapes the draft rather than proofreading it, so read it *before* writing, not after. A BE
item is argued prose: an Introduction that states its contribution up front, a Motivation that moves
from the known problem to the new result, and a Detailed design that reads cleanly on both sides.
When you fill Motivation's `TBD`, carry in the verifiable outcome that step 2's conversation already
produced — the norm requires Motivation to name it, and the answer exists by the time you draft.

**Localize the Japanese** side (the title, the `トピック`, and the prose) — the scaffolder seeds both
files from the same English input, so the Japanese is a starting point to rewrite into natural
Japanese, not a finished translation. Write it under the
[`japanese-document-writing`](../../../.apm/skills/japanese-document-writing/SKILL.md) skill (敬体; the Japanese layer beneath
`document-writing`), so both sides meet the same norm. Do **not** add an index row: the generator skips
`BE-XXXX` items, so the committed index stays row-free for the placeholder until CI numbers it.

> Why a placeholder and not a real number: IDs are permanent and monotonic, and several
> branches may be in flight at once. Picking a number by hand races — two PRs grab the
> same one. The `roadmap-id` workflow assigns the next free IDs deterministically at PR
> time, so authoring stays conflict-free.

### 5. Self-review against the CI review contract — before committing

Mirror the same review the "Claude review" GitHub Actions workflow runs (BE-0203), but locally,
before anything is committed — closing the gap between "the roadmap item reads fine to its own
author" and "the reviewer that sees it cold, on the PR, finds nothing to flag."

Run it as **two roles, never one agent** (BE-0347): one judges the diff, the other edits it, and
neither does the other's job. A model that fixes what it just flagged has every incentive to patch
just enough to silence its own comment, leaving something adjacent for the next cold look — its own
next round, or the live bot after the push — to raise again. This is the canonical procedure;
[`pr-followup`](../../../.apm/skills/pr-followup/SKILL.md), [`propose-and-build`](../../../.apm/skills/propose-and-build/SKILL.md),
and [`implement-be`](../../../.apm/skills/implement-be/SKILL.md) all run it rather than restating it. The
[`claude-review`](../../../.apm/skills/claude-review/SKILL.md) skill packages the same judge-only review/plan pass
as a standalone, directly-invocable skill; keep the two in sync if either one's cap, taxonomy, or
dedup rule changes.

**The review/plan pass.** Spawn a fresh Agent-tool subagent on `fable` that has **not** seen this
ideation conversation —
the CI reviewer also runs cold, with no memory of the authoring discussion, so a subagent that
inherited this session's context would not reproduce that. Give it exactly two inputs: the contract
at [`.github/claude-review-prompt.md`](../../../.github/claude-review-prompt.md) and the working
diff. Stage new files first with `git add roadmaps/` — `make new-roadmap-item`'s output starts out
untracked, so a bare `git diff` would omit it entirely — then run `git diff origin/main --
roadmaps/`. Scope both the add and the diff to `roadmaps/` rather than the whole tree: this skill
only ever touches that directory, and a stray file elsewhere — scratch output, unrelated
in-progress work in a parallel worktree — shouldn't get staged or reviewed along with it. There is
no PR yet, so nothing to run `gh pr diff` against. Ask it to apply every lens in the contract — the
prose-quality lenses and the functional ones alike — and skip the two parts that need a live PR:
"read the existing discussion first" (`gh pr view <PR_NUMBER> --comments`, since there is no PR
number yet) and posting findings as inline PR comments.

This pass **never edits a file**; it classifies. Every finding that clears the contract's severity
floor comes back as one of two things:

- a **fix instruction** — the file, the exact location, and the exact change to make; or
- an **escalation** — a finding that calls for a genuine design change, which is the user's call
  rather than either role's (the same valve `pr-followup` uses for a review comment asking for a
  fundamental redesign).

A false positive, or a deliberate trade-off the item already explains, is neither: it comes back
with its rationale and no instruction.

**The implement pass.** Run it on `sonnet` — this skill's fixes never leave `roadmaps/`, so the
cheaper tier is enough (BE-0347 splits the roles across models so neither inherits the other's
context). Unlike the CI workflow — which only posts comments, since prime directive 1
keeps a reviewer from also being the judge on the Tier-2 gate — this pass has no gate to stay off,
so it applies the fix instructions directly in the files. Apply them as given: don't re-judge a
finding's severity, and don't widen a change beyond what its instruction names. The judging is
already done, and scope creep here is what re-opens the diff to a fresh round of findings. If an
instruction looks unsafe or wrong, report that back rather than silently deviating from it — a
disputed instruction is the user's call, like an escalation.

**The loop.** Re-run a *fresh* review/plan pass against the updated diff after non-trivial fixes,
carrying forward this round's dismissed findings (with their rationale) into the next round's prompt
— each round's reviewer is spawned fresh with no memory of earlier dispositions, so without this a
dismissed false positive or trade-off would simply get re-flagged every round and never let the pass
come back empty. Repeat until a pass comes back empty (an empty pass is a complete review, per the
contract's own closing rule — "when nothing warrants a comment, post nothing"). "Advisory" describes
the CI workflow's relationship to the merge gate, not license to leave a real finding unfixed here.
Cap this at 3 rounds — an LLM-based reviewer is not fully deterministic and could keep surfacing a
fresh marginal finding each round, possibly one its own previous fix introduced; if the 3rd round
still returns findings, stop and let the user make the final call instead of looping further. The
cap counts **review/plan passes**, not fix attempts.

### 6. Verify

Run `make check` before finishing — roadmap changes are docs-only, but keeping the gate
green is the contract. (It needs no Simulator and runs on Linux.)

### 7. Open the PR (only when the user is happy)

Work on the session's designated branch. Commit with a scoped message
(`docs(roadmap): …`), push, and — **only if the user asked for a PR** — open it. The PR
title and body are in English. In the body, state plainly that the items carry the
`BE-XXXX` placeholder and that the **roadmap-id** workflow will allocate the real BE IDs
and push the rename back onto the branch. Don't hand-edit the numbers afterward.

## What CI does with `BE-XXXX`

[`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py), run by the
[`roadmap-id`](../../../.github/workflows/roadmap-id.yml) workflow on every PR touching
`roadmaps/**`, finds each `BE-XXXX-<slug>/` placeholder, allocates the next IDs
(`max existing BE-NNNN + 1`, sorted by slug for determinism), renames the directory and
files, rewrites `BE-XXXX` → `BE-NNNN` inside them, fixes the index-table rows by slug, and
pushes the result back to the PR branch. If there are no placeholders it's a no-op.

One limitation to respect while authoring: a brand-new item should **not** cross-reference
another brand-new item by `BE-XXXX` (the in-file rewrite is per-item). Reference already
-numbered items by their real ID, and refer to a sibling new item by name/slug in prose.
